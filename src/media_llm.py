"""Direct LLM client for audio/video processing.

Supports three providers:
- google: Gemini Files API (upload → poll → generate → cleanup)
- openrouter: base64 inline in chat completions
- ibm: base64 inline in chat completions (IBM ICA, OpenAI-compatible)
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path  # noqa: TC003
from typing import Any, cast

import httpx

from doc_convert.providers import RETRY_BACKOFF_SECONDS, RETRYABLE_HTTP_STATUS
from tracing import trace_span

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Read timeout for a single Gemini generateContent call. Long because a dense
# chunk can take several minutes to transcribe (google/ has no gateway cap).
_GEMINI_GENERATE_TIMEOUT = 30 * 60.0
# Gemini can return an empty MALFORMED_RESPONSE candidate; retry a few times.
_GEMINI_MAX_ATTEMPTS = 3

# Gateway/proxy timeout statuses seen from inline providers when a single media
# request runs past their ~10-minute request ceiling (Cloudflare 524, plus the
# standard 504/522/408 variants gateways emit for the same condition).
_GATEWAY_TIMEOUT_CODES = frozenset({408, 504, 522, 524})

# Transient upstream failures, shared with the other provider call paths (see
# providers.RETRYABLE_HTTP_STATUS). A 502 on a small part is transient, not "too
# large" (part 1 of a recording succeeds, part 2 of the same size 502s, then
# retries fine). We retry these with exponential backoff before failing.
_RETRYABLE_STATUS = RETRYABLE_HTTP_STATUS
_MAX_MEDIA_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = RETRY_BACKOFF_SECONDS
# A raw payload above this genuinely exceeds the inline base64 ceiling (~75 MB
# base64 ≈ ~56 MB raw, measured against IBM ICA). A 502/413 here really IS "too
# large" and retrying cannot help, so we fail fast and point at google/.
_INLINE_TOO_LARGE_MB = 60.0


def post_with_retry(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    timeout: float = 300.0,
    provider_label: str = "llm",
    operation: str = "request",
) -> httpx.Response:
    """POST to an LLM endpoint, retrying transient 5xx/429 with exponential backoff.

    Intended for text-only chat-completions calls (companion analysis, document
    analysis, meeting summary, note metadata, PPTX slide VLM) that previously
    called ``raise_for_status()`` directly without any retry. The ``media_llm``
    already wraps multimodal requests with ``_send_media_request``; this covers
    the remaining call sites.

    Raises ``httpx.HTTPStatusError`` on non-retryable status after retries are
    exhausted so callers' existing error handling is preserved.
    """
    last_resp: httpx.Response | None = None
    for attempt in range(1, _MAX_MEDIA_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt < _MAX_MEDIA_ATTEMPTS:
                wait = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "%s %s transport error (%s, attempt %d/%d); retrying in %.0fs",
                    provider_label,
                    operation,
                    type(exc).__name__,
                    attempt,
                    _MAX_MEDIA_ATTEMPTS,
                    wait,
                )
                time.sleep(wait)
                continue
            raise
        if resp.is_success:
            return resp
        status = resp.status_code
        if status in _RETRYABLE_STATUS and attempt < _MAX_MEDIA_ATTEMPTS:
            wait = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
            logger.warning(
                "%s %s returned %d (transient, attempt %d/%d); retrying in %.0fs",
                provider_label,
                operation,
                status,
                attempt,
                _MAX_MEDIA_ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            last_resp = resp
            continue
        resp.raise_for_status()  # non-retryable: propagate immediately
        return resp  # unreachable, raise_for_status throws
    # All retries exhausted on a retryable status. Not an assert: under python -O
    # it would vanish and turn the intended HTTP error into an AttributeError.
    if last_resp is None:  # pragma: no cover - the retry branch always sets it
        raise RuntimeError(f"{provider_label} {operation} exhausted retries without any response")
    last_resp.raise_for_status()
    return last_resp  # unreachable


def _too_large_message(provider_label: str, file_name: str, size_mb: float) -> str:
    return (
        f"File too large for {provider_label} ({size_mb:.0f} MB raw payload).\n"
        "Inline media providers have payload size limits.\n\n"
        "Use the google/ provider instead (uploads via Files API, no size limit):\n"
        f"  doc-convert {file_name} --llm google/gemini-3.1-pro-preview\n"
        "  (requires DOC_CONVERT_GOOGLE_API_KEY env var)"
    )


def _gateway_timeout_message(provider_label: str, file_name: str) -> str:
    return (
        f"{provider_label} gateway timed out processing {file_name} after "
        f"{_MAX_MEDIA_ATTEMPTS} attempts.\nThe request exceeded the provider's "
        "~10-minute ceiling. doc-convert already splits audio by duration to "
        "avoid this, so a persistent timeout means unusually dense media.\n\n"
        "Use the google/ provider instead (Files API, no gateway timeout):\n"
        f"  doc-convert {file_name} --llm google/gemini-3.1-pro-preview\n"
        "  (requires DOC_CONVERT_GOOGLE_API_KEY env var)"
    )


def _transient_gateway_message(provider_label: str, file_name: str) -> str:
    return (
        f"{provider_label} kept returning a transient gateway error for {file_name} "
        f"after {_MAX_MEDIA_ATTEMPTS} attempts.\nThis is an upstream flake, not a "
        "payload problem. Re-run to retry, or use the google/ provider (different "
        "transport):\n"
        f"  doc-convert {file_name} --llm google/gemini-3.1-pro-preview\n"
        "  (requires DOC_CONVERT_GOOGLE_API_KEY env var)"
    )


def _send_media_request(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    *,
    provider_label: str,
    file_name: str,
    raw_size_mb: float,
) -> httpx.Response:
    """POST a media request, retrying transient gateway failures with backoff.

    Returns the successful response, or raises ``RuntimeError`` with an
    actionable message once retries are exhausted (or immediately for a genuine
    payload-too-large, which retrying cannot fix).
    """
    resp: httpx.Response | None = None
    for attempt in range(1, _MAX_MEDIA_ATTEMPTS + 1):
        # A slow/hung gateway raises an httpx exception (ReadTimeout, ConnectError,
        # RemoteProtocolError, ...) instead of an HTTP status. IBM ICA does this for
        # a request that runs long: it holds the socket open sending nothing until
        # our read timeout fires. Treat these exactly like a retryable transient.
        try:
            resp = client.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt < _MAX_MEDIA_ATTEMPTS:
                wait = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "%s request failed (%s, attempt %d/%d); retrying in %.0fs",
                    provider_label,
                    type(exc).__name__,
                    attempt,
                    _MAX_MEDIA_ATTEMPTS,
                    wait,
                )
                time.sleep(wait)
                continue
            logger.error("%s request failed (%s) after %d attempts", provider_label, type(exc).__name__, attempt)
            raise RuntimeError(_gateway_timeout_message(provider_label, file_name)) from exc
        if resp.is_success:
            return resp
        status = resp.status_code
        # Genuine payload-too-large (413 always; 502 only when the raw payload is
        # near/over the inline ceiling): retrying cannot help, fail straight over.
        if status == 413 or (status == 502 and raw_size_mb > _INLINE_TOO_LARGE_MB):  # noqa: PLR2004
            logger.error("%s returned %d: payload too large (%.1f MB raw)", provider_label, status, raw_size_mb)
            raise RuntimeError(_too_large_message(provider_label, file_name, raw_size_mb))
        if status in _RETRYABLE_STATUS and attempt < _MAX_MEDIA_ATTEMPTS:
            wait = _RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
            logger.warning(
                "%s returned %d (transient, attempt %d/%d); retrying in %.0fs",
                provider_label,
                status,
                attempt,
                _MAX_MEDIA_ATTEMPTS,
                wait,
            )
            time.sleep(wait)
            continue
        break

    # Retries exhausted, or a non-retryable status on the first try.
    if resp is None:  # pragma: no cover - the loop always runs at least once
        msg = f"{provider_label} produced no response"
        raise RuntimeError(msg)
    status = resp.status_code
    if status in _GATEWAY_TIMEOUT_CODES:
        logger.error("%s returned %d: gateway timeout after %d attempts", provider_label, status, _MAX_MEDIA_ATTEMPTS)
        raise RuntimeError(_gateway_timeout_message(provider_label, file_name))
    if status == 502:  # noqa: PLR2004
        logger.error("%s returned 502 after %d attempts (transient)", provider_label, _MAX_MEDIA_ATTEMPTS)
        raise RuntimeError(_transient_gateway_message(provider_label, file_name))
    logger.error("%s error %d: %s", provider_label, status, resp.text[:500])
    resp.raise_for_status()
    msg = f"{provider_label} request failed with status {status}"  # defensive: raise_for_status already threw
    raise RuntimeError(msg)


# ── MIME types ───────────────────────────────────────────────────────────────

IMAGE_MIME: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def is_image_ext(ext: str) -> bool:
    return ext.lower() in IMAGE_MIME


def get_image_mime(path: Path) -> str:
    return IMAGE_MIME.get(path.suffix.lower(), "image/png")


AUDIO_MIME: dict[str, str] = {
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mp3",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
    ".webm": "audio/webm",
    ".3gp": "audio/3gpp",
}

VIDEO_MIME: dict[str, str] = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".webm": "video/webm",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
    ".3gp": "video/3gpp",
}


def get_media_mime(path: Path) -> str:
    """Get MIME type for an audio or video file."""
    ext = path.suffix.lower()
    return AUDIO_MIME.get(ext) or VIDEO_MIME.get(ext, "application/octet-stream")


def is_audio_ext(ext: str) -> bool:
    """Check if extension is a known audio format."""
    return ext.lower() in AUDIO_MIME


def is_video_ext(ext: str) -> bool:
    """Check if extension is a known video format."""
    return ext.lower() in VIDEO_MIME


# ── Gemini Files API ─────────────────────────────────────────────────────────


def _gemini_upload(file_path: Path, mime_type: str, api_key: str) -> dict[str, Any]:
    """Upload a file to Gemini Files API (resumable protocol)."""
    num_bytes = file_path.stat().st_size
    display_name = file_path.name

    # Scale timeout based on file size: 300s per 10MB (minimum 300s)
    upload_timeout = max(300.0, 300.0 * (num_bytes / (10 * 1024 * 1024)))
    logger.info("Uploading %.1f MB (timeout: %.0fs)", num_bytes / (1024 * 1024), upload_timeout)
    with httpx.Client(timeout=httpx.Timeout(upload_timeout, connect=30.0)) as client:
        # Step 1: initiate upload
        resp = client.post(
            f"{GEMINI_API_BASE}/upload/v1beta/files",
            headers={
                "x-goog-api-key": api_key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(num_bytes),
                "X-Goog-Upload-Header-Content-Type": mime_type,
                "Content-Type": "application/json",
            },
            json={"file": {"display_name": display_name}},
        )
        resp.raise_for_status()
        upload_url = resp.headers["x-goog-upload-url"]

        # Step 2: upload bytes
        data = file_path.read_bytes()
        resp = client.post(
            upload_url,
            headers={
                "Content-Length": str(num_bytes),
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            content=data,
        )
        resp.raise_for_status()

    return cast("dict[str, Any]", resp.json()["file"])


def _gemini_poll(file_name: str, api_key: str, poll_interval: float = 2.0) -> None:
    """Poll Gemini Files API until file state is ACTIVE."""
    with httpx.Client(timeout=30.0) as client:
        while True:
            resp = client.get(
                f"{GEMINI_API_BASE}/v1beta/{file_name}",
                headers={"x-goog-api-key": api_key},
            )
            resp.raise_for_status()
            state = resp.json()["state"]
            if state == "ACTIVE":
                return
            if state == "FAILED":
                error = resp.json().get("error", "unknown error")
                msg = f"Gemini file processing failed: {error}"
                raise RuntimeError(msg)
            logger.debug("File %s state: %s, polling...", file_name, state)
            time.sleep(poll_interval)


def _gemini_generate(
    file_uri: str,
    mime_type: str,
    model: str,
    prompt: str,
    api_key: str,
    *,
    system_prompt: str | None = None,
    extra_files: list[tuple[str, str]] | None = None,
) -> str:
    """Generate content using an uploaded Gemini file.

    ``extra_files`` is a list of ``(file_uri, mime_type)`` tuples to attach
    alongside the primary media (e.g. companion whiteboard images).
    """
    parts: list[dict[str, Any]] = [
        {"text": prompt},
        {"file_data": {"mime_type": mime_type, "file_uri": file_uri}},
    ]
    for uri, mime in extra_files or []:
        parts.append({"file_data": {"mime_type": mime, "file_uri": uri}})
    body: dict[str, Any] = {"contents": [{"parts": parts}]}
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    url = f"{GEMINI_API_BASE}/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

    # Gemini occasionally returns finishReason=MALFORMED_RESPONSE with an empty
    # candidate (seen on very large single-shot transcriptions); it is sometimes
    # transient, so retry a couple of times before giving up. A genuinely oversized
    # request keeps malforming, which is why long audio is chunked upstream.
    last_finish = "unknown"
    for attempt in range(1, _GEMINI_MAX_ATTEMPTS + 1):
        # Generous read timeout: unlike IBM ICA (hard ~10-min gateway cap), Google
        # has no proxy cutting the connection, so a long transcription just needs
        # us to wait for the buffered response.
        with httpx.Client(timeout=httpx.Timeout(_GEMINI_GENERATE_TIMEOUT, connect=30.0)) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()

        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates:
            reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
            logger.error("Gemini returned no candidates. Block reason: %s. Response: %s", reason, str(data)[:500])
            msg = f"Gemini returned no candidates (block reason: {reason})"
            raise RuntimeError(msg)

        parts_out = candidates[0].get("content", {}).get("parts", [])
        if parts_out:
            return cast("str", parts_out[0].get("text", ""))

        last_finish = candidates[0].get("finishReason", "unknown")
        logger.warning(
            "Gemini candidate has no parts (finish reason: %s, attempt %d/%d)",
            last_finish,
            attempt,
            _GEMINI_MAX_ATTEMPTS,
        )
        if attempt < _GEMINI_MAX_ATTEMPTS:
            time.sleep(_RETRY_BACKOFF_SECONDS[min(attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)])

    logger.error("Gemini produced no content after %d attempts (finish reason: %s)", _GEMINI_MAX_ATTEMPTS, last_finish)
    msg = (
        f"Gemini produced no content (finish reason: {last_finish}). For a long recording this means the "
        "single-shot transcription was too large; doc-convert chunks long audio to avoid it."
    )
    raise RuntimeError(msg)


def _gemini_delete(file_name: str, api_key: str) -> None:
    """Delete an uploaded file from Gemini."""
    try:
        with httpx.Client(timeout=30.0) as client:
            client.delete(
                f"{GEMINI_API_BASE}/v1beta/{file_name}",
                headers={"x-goog-api-key": api_key},
            )
    except Exception:
        logger.warning("Failed to delete Gemini file %s", file_name)


def process_media_google(
    file_path: Path,
    model: str,
    prompt: str,
    api_key: str,
    *,
    system_prompt: str | None = None,
    attachments: list[Path] | None = None,
) -> str:
    """Process audio/video via Google Gemini Files API: upload → poll → generate → cleanup.

    ``attachments`` is an optional list of additional image files (e.g. whiteboard
    photos referenced in the companion notes) uploaded alongside the primary
    media and made available to the model in the same generate call.
    """
    mime_type = get_media_mime(file_path)
    uploaded_names: list[str] = []
    extra_file_data: list[tuple[str, str]] = []

    try:
        with trace_span("gemini.upload", file=file_path.name, mime=mime_type):
            logger.info("Uploading %s to Gemini (%s)", file_path.name, mime_type)
            file_info = _gemini_upload(file_path, mime_type, api_key)
            file_uri = file_info["uri"]
            file_name = file_info["name"]
            uploaded_names.append(file_name)

        if file_info.get("state") != "ACTIVE":
            with trace_span("gemini.poll", file=file_name):
                logger.info("Waiting for Gemini to process %s...", file_path.name)
                _gemini_poll(file_name, api_key)

        for att in attachments or []:
            att_mime = get_image_mime(att) if is_image_ext(att.suffix) else get_media_mime(att)
            with trace_span("gemini.upload", file=att.name, mime=att_mime):
                logger.info("Uploading attachment %s (%s)", att.name, att_mime)
                att_info = _gemini_upload(att, att_mime, api_key)
                uploaded_names.append(att_info["name"])
                if att_info.get("state") != "ACTIVE":
                    _gemini_poll(att_info["name"], api_key)
                extra_file_data.append((att_info["uri"], att_mime))

        with trace_span("gemini.generate", model=model, attachments=len(extra_file_data)):
            logger.info("Generating with %s (+%d attachment(s))", model, len(extra_file_data))
            return _gemini_generate(
                file_uri,
                mime_type,
                model,
                prompt,
                api_key,
                system_prompt=system_prompt,
                extra_files=extra_file_data,
            )
    finally:
        for name in uploaded_names:
            _gemini_delete(name, api_key)


# ── OpenRouter API ───────────────────────────────────────────────────────────


def process_media_openrouter(
    file_path: Path,
    model: str,
    prompt: str,
    api_key: str,
    *,
    system_prompt: str | None = None,
    url: str = OPENROUTER_API_URL,
    provider_label: str = "openrouter",
    attachments: list[Path] | None = None,
) -> str:
    """Process audio/video via an OpenAI-compatible endpoint with base64 inline.

    Used by OpenRouter and IBM ICA (and any other chat-completions provider).
    ``attachments`` (optional image files) are sent inline as additional
    ``image_url`` content parts.
    """
    mime_type = get_media_mime(file_path)
    b64_data = base64.b64encode(file_path.read_bytes()).decode()

    is_audio = is_audio_ext(file_path.suffix)

    if is_audio:
        fmt = file_path.suffix.lstrip(".")
        if fmt == "ogg":
            fmt = "wav"  # OpenRouter may not support ogg, use wav
        media_part: dict[str, Any] = {
            "type": "input_audio",
            "input_audio": {"data": b64_data, "format": fmt},
        }
    else:
        media_part = {
            "type": "video_url",
            "video_url": {"url": f"data:{mime_type};base64,{b64_data}"},
        }

    content_parts: list[dict[str, Any]] = [
        {"type": "text", "text": prompt},
        media_part,
    ]
    for att in attachments or []:
        if not is_image_ext(att.suffix):
            logger.warning("Skipping non-image attachment for inline provider: %s", att.name)
            continue
        att_mime = get_image_mime(att)
        att_b64 = base64.b64encode(att.read_bytes()).decode()
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{att_mime};base64,{att_b64}"},
            }
        )

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content_parts})

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": messages}
    raw_size_mb = file_path.stat().st_size / (1024 * 1024)

    with trace_span(f"{provider_label}.generate", model=model, media_type="audio" if is_audio else "video"):
        logger.info("Sending %s to %s (%s, %s)", file_path.name, provider_label, model, mime_type)
        with httpx.Client(timeout=600.0) as client:
            resp = _send_media_request(
                client,
                url,
                headers,
                body,
                provider_label=provider_label,
                file_name=file_path.name,
                raw_size_mb=raw_size_mb,
            )

    return cast("str", resp.json()["choices"][0]["message"]["content"])


# ── Unified entry point ──────────────────────────────────────────────────────


def process_media(
    file_path: Path,
    provider: str,
    model: str,
    prompt: str,
    api_key: str,
    *,
    system_prompt: str | None = None,
    url: str | None = None,
    attachments: list[Path] | None = None,
) -> str:
    """Process audio/video with the specified provider.

    ``url`` is required for ``ibm`` (and any other OpenAI-compatible provider
    with a non-default endpoint); ignored for ``google``.

    ``attachments`` is an optional list of extra image files (e.g. whiteboard
    photos) to send alongside the primary media in the multimodal call.
    """
    if provider == "google":
        return process_media_google(
            file_path, model, prompt, api_key, system_prompt=system_prompt, attachments=attachments
        )
    if provider == "openrouter":
        return process_media_openrouter(
            file_path, model, prompt, api_key, system_prompt=system_prompt, attachments=attachments
        )
    if provider == "ibm":
        if not url:
            msg = "ibm provider requires `url` (resolve via providers.get_provider_url)"
            raise ValueError(msg)
        return process_media_openrouter(
            file_path,
            model,
            prompt,
            api_key,
            system_prompt=system_prompt,
            url=url,
            provider_label="ibm",
            attachments=attachments,
        )
    msg = f"Unsupported provider for media: {provider}"
    raise ValueError(msg)
