"""Direct LLM client for audio/video processing.

Supports two providers:
- google: Gemini Files API (upload → poll → generate → cleanup)
- openrouter: base64 inline in chat completions
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path  # noqa: TC003

import httpx

from tracing import trace_span

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# ── MIME types ───────────────────────────────────────────────────────────────

AUDIO_MIME: dict[str, str] = {
    ".ogg": "audio/ogg",
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


def _gemini_upload(file_path: Path, mime_type: str, api_key: str) -> dict:
    """Upload a file to Gemini Files API (resumable protocol)."""
    num_bytes = file_path.stat().st_size
    display_name = file_path.name

    with httpx.Client(timeout=300.0) as client:
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

    return resp.json()["file"]


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
) -> str:
    """Generate content using an uploaded Gemini file."""
    parts: list[dict] = [
        {"text": prompt},
        {"file_data": {"mime_type": mime_type, "file_uri": file_uri}},
    ]
    body: dict = {"contents": [{"parts": parts}]}
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

    return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


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
) -> str:
    """Process audio/video via Google Gemini Files API: upload → poll → generate → cleanup."""
    mime_type = get_media_mime(file_path)

    with trace_span("gemini.upload", file=file_path.name, mime=mime_type):
        logger.info("Uploading %s to Gemini (%s)", file_path.name, mime_type)
        file_info = _gemini_upload(file_path, mime_type, api_key)
        file_uri = file_info["uri"]
        file_name = file_info["name"]

    if file_info.get("state") != "ACTIVE":
        with trace_span("gemini.poll", file=file_name):
            logger.info("Waiting for Gemini to process %s...", file_path.name)
            _gemini_poll(file_name, api_key)

    try:
        with trace_span("gemini.generate", model=model):
            logger.info("Generating with %s", model)
            return _gemini_generate(file_uri, mime_type, model, prompt, api_key, system_prompt=system_prompt)
    finally:
        _gemini_delete(file_name, api_key)


# ── OpenRouter API ───────────────────────────────────────────────────────────


def process_media_openrouter(
    file_path: Path,
    model: str,
    prompt: str,
    api_key: str,
    *,
    system_prompt: str | None = None,
) -> str:
    """Process audio/video via OpenRouter with base64 inline."""
    mime_type = get_media_mime(file_path)
    b64_data = base64.b64encode(file_path.read_bytes()).decode()

    is_audio = is_audio_ext(file_path.suffix)

    if is_audio:
        fmt = file_path.suffix.lstrip(".")
        if fmt == "ogg":
            fmt = "wav"  # OpenRouter may not support ogg, use wav
        media_part: dict = {
            "type": "input_audio",
            "input_audio": {"data": b64_data, "format": fmt},
        }
    else:
        media_part = {
            "type": "video_url",
            "video_url": {"url": f"data:{mime_type};base64,{b64_data}"},
        }

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                media_part,
            ],
        }
    )

    with trace_span("openrouter.generate", model=model, media_type="audio" if is_audio else "video"):
        logger.info("Sending %s to OpenRouter (%s, %s)", file_path.name, model, mime_type)
        with httpx.Client(timeout=600.0) as client:
            resp = client.post(
                OPENROUTER_API_URL,
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
                        "OpenRouter returned 502: file too large (%.1f MB as base64). "
                        "Use google/ provider with Files API instead.",
                        size_mb,
                    )
                    msg = (
                        f"File too large for OpenRouter ({size_mb:.0f} MB as base64 payload).\n"
                        "OpenRouter has payload size limits for inline media.\n\n"
                        "Use the google/ provider instead (uploads via Files API, no size limit):\n"
                        f"  doc-convert {file_path.name} --use-external-llm google/gemini-2.5-flash\n"
                        "  (requires GOOGLE_API_KEY env var)"
                    )
                    raise RuntimeError(msg)
                error_body = resp.text[:500]
                logger.error("OpenRouter error %d: %s", resp.status_code, error_body)
                resp.raise_for_status()

    return resp.json()["choices"][0]["message"]["content"]


# ── Unified entry point ──────────────────────────────────────────────────────


def process_media(
    file_path: Path,
    provider: str,
    model: str,
    prompt: str,
    api_key: str,
    *,
    system_prompt: str | None = None,
) -> str:
    """Process audio/video with the specified provider."""
    if provider == "google":
        return process_media_google(file_path, model, prompt, api_key, system_prompt=system_prompt)
    if provider == "openrouter":
        return process_media_openrouter(file_path, model, prompt, api_key, system_prompt=system_prompt)
    msg = f"Unsupported provider for media: {provider}"
    raise ValueError(msg)
