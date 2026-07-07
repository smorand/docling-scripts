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

from tracing import trace_span

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Gateway/proxy timeout statuses seen from inline providers when a single media
# request runs past their ~10-minute request ceiling (Cloudflare 524, plus the
# standard 502/504/522/408 variants gateways emit for the same condition).
_GATEWAY_TIMEOUT_CODES = frozenset({408, 504, 522, 524})

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

    with httpx.Client(timeout=600.0) as client:
        resp = client.post(
            f"{GEMINI_API_BASE}/v1beta/models/{model}:generateContent",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json=body,
        )
        resp.raise_for_status()

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        reason = data.get("promptFeedback", {}).get("blockReason", "unknown")
        logger.error("Gemini returned no candidates. Block reason: %s. Response: %s", reason, str(data)[:500])
        msg = f"Gemini returned no candidates (block reason: {reason})"
        raise RuntimeError(msg)

    content = candidates[0].get("content", {})
    parts = content.get("parts", [])
    if not parts:
        finish = candidates[0].get("finishReason", "unknown")
        logger.error("Gemini candidate has no parts. Finish reason: %s. Response: %s", finish, str(data)[:500])
        msg = f"Gemini candidate has no content parts (finish reason: {finish})"
        raise RuntimeError(msg)

    return cast("str", parts[0].get("text", ""))


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

    with trace_span(f"{provider_label}.generate", model=model, media_type="audio" if is_audio else "video"):
        logger.info("Sending %s to %s (%s, %s)", file_path.name, provider_label, model, mime_type)
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "messages": messages},
            )
            if not resp.is_success:
                if resp.status_code == 502:  # noqa: PLR2004
                    size_mb = file_path.stat().st_size / (1024 * 1024)
                    logger.error(
                        "%s returned 502: file too large (%.1f MB as base64). "
                        "Use google/ provider with Files API instead.",
                        provider_label,
                        size_mb,
                    )
                    msg = (
                        f"File too large for {provider_label} ({size_mb:.0f} MB as base64 payload).\n"
                        "Inline media providers have payload size limits.\n\n"
                        "Use the google/ provider instead (uploads via Files API, no size limit):\n"
                        f"  doc-convert {file_path.name} --llm google/gemini-3.1-pro-preview\n"
                        "  (requires GOOGLE_API_KEY env var)"
                    )
                    raise RuntimeError(msg)
                if resp.status_code in _GATEWAY_TIMEOUT_CODES:
                    logger.error(
                        "%s returned %d: gateway timeout. The media chunk took too long to process.",
                        provider_label,
                        resp.status_code,
                    )
                    msg = (
                        f"{provider_label} gateway timed out ({resp.status_code}) processing "
                        f"{file_path.name}.\nThe request exceeded the provider's ~10-minute ceiling. "
                        "This normally means a very long recording; doc-convert splits audio by "
                        "duration to avoid it, so if you hit this, the media is unusually dense.\n\n"
                        "Use the google/ provider instead (Files API, no gateway timeout):\n"
                        f"  doc-convert {file_path.name} --llm google/gemini-3.1-pro-preview\n"
                        "  (requires GOOGLE_API_KEY env var)"
                    )
                    raise RuntimeError(msg)
                error_body = resp.text[:500]
                logger.error("%s error %d: %s", provider_label, resp.status_code, error_body)
                resp.raise_for_status()

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
