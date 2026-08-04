"""External LLM provider configuration and API key management."""

from __future__ import annotations

import typer

from config import Settings  # noqa: TC001
from logging_config import console

_DEFAULT_LLM_PROMPT = (
    "Convert this document page to well-structured markdown. "
    "Extract ALL text precisely.\n\n"
    "For administrative documents, clearly identify and highlight:\n"
    "- Personal identifiers (passport numbers, ID numbers, client numbers, "
    "social security numbers)\n"
    "- Credentials (login, passwords, access codes)\n"
    "- Dates (issue dates, expiry dates, deadlines, birth dates)\n"
    "- Locations (addresses, cities, countries)\n"
    "- People and their roles (signatories, mandated persons, "
    "representatives, beneficiaries)\n"
    "- Financial amounts (costs, revenues, taxes, fees, totals "
    "with currency)\n"
    "- Reference numbers (invoice numbers, contract numbers, case numbers)\n\n"
    "Format these as bold or in a clearly labeled section. "
    "Do not miss any text. Output only the bare markdown."
)


def get_external_llm_prompt() -> str:
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("document", "llm_conversion_prompt", _DEFAULT_LLM_PROMPT)


# Keep module-level alias for backward compat (used by image.py)
EXTERNAL_LLM_PROMPT = _DEFAULT_LLM_PROMPT


_DEFAULT_CAPTION_PROMPT = (
    "Describe this image in rich detail for a document catalog. Cover, in order:\n\n"
    "1. **What it depicts**: subject, context, key elements, layout, colors. "
    "Be concrete and specific.\n"
    "2. **Image type**: photo, chart, diagram, screenshot, logo, scan, "
    "handwritten note, etc.\n"
    "3. **All visible text**: transcribe every readable text fragment exactly as it appears. "
    "Use **bold** for identifiers, labels, numbers, dates, references, names, "
    "addresses, and any administrative or technical information.\n"
    "4. **If chart/diagram**: chart type, axes, scales, values, trends, what it shows.\n"
    "5. **If table embedded in the image**: reproduce as a markdown table.\n\n"
    "Output only the description in markdown, no preamble or closing remarks. "
    "Be thorough; do not omit visible text."
)

_DEFAULT_TABLE_PROMPT = (
    "Summarize this table for a downstream RAG/LLM consumer in 2-4 sentences.\n\n"
    "Cover:\n"
    "- What the table measures (subject + unit).\n"
    "- Its dimensions (what the rows and columns represent).\n"
    "- Key magnitudes (max, min, totals, ranges) with explicit numbers.\n"
    "- Any obvious trend, outlier, or comparison worth flagging.\n\n"
    "Be concrete; quote numbers verbatim. Output the summary only, no preamble."
)


def get_caption_prompt() -> str:
    """Prompt used to describe a single extracted figure."""
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("document", "caption_prompt", _DEFAULT_CAPTION_PROMPT)


def get_table_prompt() -> str:
    """Prompt used to summarize an extracted table."""
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("document", "table_prompt", _DEFAULT_TABLE_PROMPT)


_DEFAULT_OCR_PROMPT = (
    "Transcribe ALL text visible in this image exactly as it appears, "
    "preserving the reading order and line breaks. Reproduce numbers, labels, "
    "and punctuation verbatim. Do not translate, summarize, describe, or add "
    "any commentary. Output only the raw transcribed text, with no markdown "
    "code fences and no preamble."
)


def get_ocr_prompt() -> str:
    """Prompt used by the LLM OCR engine to transcribe a single image region."""
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("document", "ocr_prompt", _DEFAULT_OCR_PROMPT)


_DEFAULT_MEETING_SUMMARY_CSS = """\
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
        }
        h1 {
            color: #0078d4;
            font-size: 1.5em;
            border-bottom: 3px solid #0078d4;
            padding-bottom: 10px;
        }
        h2 {
            font-size: 1.2em;
            color: #106ebe;
            margin-top: 25px;
        }
        ul { margin: 10px 0; }
        li { margin: 8px 0; }
        table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        th { background-color: #0078d4; color: white; padding: 12px; text-align: left; }
        td { border: 1px solid #ddd; padding: 10px; }
        tr:nth-child(even) { background-color: #f9f9f9; }
        strong { color: #106ebe; }
"""


_DEFAULT_MEETING_SUMMARY_PROMPT = """\
You are producing an HTML meeting summary for executive review.

Inputs available to you:
- The audio transcription with speaker diarization (provided as document.md).
- Optional meeting context (calendar event, additional documents) provided
  in the system prompt as "Meeting Context".

Output: a single complete HTML document (with <html>, <head>, <body>) that
will be opened in a browser for human validation.

# Required sections, in order

1. Meeting Information (icon: 📅)
   - Title, date, agenda (if known from context).

2. Attendees (icon: 👥)
   - Bullet list. Mark absent attendees as "<i>Name (abs)</i>".
   - People only cited but not present MUST NOT appear here.

3. Topics discussed (icon: 💬)
   - One <h2> per topic, followed by 2-5 sentences of substance.
   - Quote concrete facts (numbers, dates, names, technical terms).

4. Decisions (icon: ✅)
   - Bullet list of every decision closed during the meeting.
   - If none: "<i>No decision</i>".

5. Actions (icon: 🎯)
   - HTML table with columns Label | Owner | ETA.
   - If owner unknown: "<i>not provided</i>". Same for ETA.
   - If none: "<i>No action</i>".

# Style

Use this exact CSS in <head><style>...</style></head>:
{css}

# Rules

- Output a complete <!DOCTYPE html><html>...</html> document. No preamble,
  no closing remarks.
- Write in the same language as the transcript.
- Be specific: prefer extracted facts over vague summaries.
- Do not invent attendees, decisions, or actions that are not in the input.
- If the meeting context contains attachments, list them as bullets at the
  end of the Topics section under a sub-heading "Reference materials".
"""


def get_meeting_summary_prompt() -> str:
    """System prompt used by ``--meeting-summary`` to produce the HTML."""
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    template = get_prompt("meeting_summary", "system_prompt", _DEFAULT_MEETING_SUMMARY_PROMPT)
    css = get_prompt("meeting_summary", "css", _DEFAULT_MEETING_SUMMARY_CSS)
    return template.format(css=css)


def build_context_block(caption: str = "", mention: str = "") -> str:
    """Build an optional context block to prepend to a caption/table prompt.

    Both fields are optional; an empty block (no caption, no mention) returns "".
    """
    parts: list[str] = []
    if caption:
        parts.append(f"Document caption: {caption.strip()}")
    if mention:
        parts.append(f"Mentioned in document: {mention.strip()}")
    if not parts:
        return ""
    return "Context from the source document:\n" + "\n".join(f"- {p}" for p in parts) + "\n\n"


# Static URLs for providers with a fixed endpoint. `ibm` is dynamic, resolved
# at runtime from settings.ibm_ica_base_url. Use get_provider_url() to read.
PROVIDER_URLS: dict[str, str] = {
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}

SUPPORTED_PROVIDERS: tuple[str, ...] = ("google", "openrouter", "ibm")

# Video default uses google/ (Gemini Files API: upload → generate), which has no
# payload size limit. Inline-base64 providers (ibm/, openrouter/) fail with 502
# on large video. gemini-3-pro-preview was retired on the Google API ("no longer
# available"); gemini-3.1-pro-preview is its current successor.
DEFAULT_MEDIA_LLM = "google/gemini-3.1-pro-preview"

# Audio defaults to ibm/ (IBM ICA fronts Gemini): audio is normalised to mono
# 16 kHz ogg and split when needed (see audio_prep) so it always fits the inline
# payload limit, letting transcription run on the single IBM credential without
# GOOGLE_API_KEY.
DEFAULT_AUDIO_LLM = "ibm/gemini-3.1-pro-preview"

# Default model for the `--analyze` text-only analysis pass on documents.
# Picked for reasoning quality on long markdown context. Document analysis only
# sends text, so any provider works (no Files API constraint).
DEFAULT_DOCUMENT_ANALYSIS_LLM = "ibm/claude-opus-4-8"

# Default model for companion context analysis. Must be multimodal (companion
# notes often reference screenshots/whiteboard captures sent inline). Gemini
# 3.1 Pro via IBM ICA is used because it supports vision and is already the
# default for audio transcription.
DEFAULT_COMPANION_LLM = "ibm/gemini-3.1-pro-preview"

# Default model for the PPTX whole-slide screenshot visual interpretation pass
# (see pptx_slide_vlm.py). Confirmed available via IBM ICA's GET /models:
# 'claude-sonnet-4-6' is listed alongside claude-sonnet-4-5/claude-opus-4-8.
DEFAULT_PPTX_SLIDE_VLM = "ibm/claude-sonnet-4-6"

# Default model for image conversion when the input itself is an image and the
# user did not pass --llm. Images always go through the external VLM pipeline,
# so we auto-fill a small IBM-hosted Claude model instead of hard-failing.
DEFAULT_IMAGE_LLM = "ibm/claude-sonnet-4-5"


def get_provider_url(provider: str, settings: Settings) -> str:
    """Return the OpenAI-compatible chat completions URL for a provider."""
    if provider in PROVIDER_URLS:
        return PROVIDER_URLS[provider]
    if provider == "ibm":
        if not settings.ibm_ica_base_url:
            console.print("[red]IBM_ICA_BASE_URL env var is required for ibm/ provider[/red]")
            raise typer.Exit(1)
        return f"{settings.ibm_ica_base_url.rstrip('/')}/chat/completions"
    console.print(f"[red]Unknown provider: {provider}[/red]")
    raise typer.Exit(1)


def parse_external_llm(value: str) -> tuple[str, str]:
    """Parse 'provider/model' into (provider, model).

    Examples:
        google/gemini-3.1-flash-preview -> ("google", "gemini-3.1-flash-preview")
        openrouter/google/gemini-3.1-pro-preview -> ("openrouter", "google/gemini-3.1-pro-preview")
        ibm/gemini-3.1-pro-preview -> ("ibm", "gemini-3.1-pro-preview")
    """
    for provider in SUPPORTED_PROVIDERS:
        prefix = f"{provider}/"
        if value.startswith(prefix):
            model = value[len(prefix) :]
            if not model:
                console.print(f"[red]Missing model name after '{prefix}'[/red]")
                raise typer.Exit(1)
            return provider, model
    supported = ", ".join(f"{p}/<model>" for p in SUPPORTED_PROVIDERS)
    console.print(f"[red]Unknown provider in '{value}'. Supported: {supported}[/red]")
    raise typer.Exit(1)


def require_api_key(provider: str, settings: Settings) -> str:
    """Get the API key for a provider, or exit with error."""
    if provider == "google":
        if not settings.google_api_key:
            console.print("[red]GOOGLE_API_KEY env var is required for google/ provider[/red]")
            raise typer.Exit(1)
        return settings.google_api_key
    if provider == "openrouter":
        if not settings.openrouter_api_key:
            console.print("[red]OPENROUTER_API_KEY env var is required for openrouter/ provider[/red]")
            raise typer.Exit(1)
        return settings.openrouter_api_key
    if provider == "ibm":
        if not settings.ibm_ica_model_key:
            console.print("[red]IBM_ICA_MODEL_KEY env var is required for ibm/ provider[/red]")
            raise typer.Exit(1)
        return settings.ibm_ica_model_key
    console.print(f"[red]Unknown provider: {provider}[/red]")
    raise typer.Exit(1)


def resolve_media_llm(llm: str | None, settings: Settings, *, media_type: str = "video") -> tuple[str, str, str]:
    """Resolve provider, model, and API key for audio/video processing.

    When ``llm`` is not given, the default depends on ``media_type``: audio uses
    ``DEFAULT_AUDIO_LLM`` (ibm/, since audio is prepped to fit the inline limit),
    video uses ``DEFAULT_MEDIA_LLM`` (google/, for the size-unlimited Files API).
    """
    default = DEFAULT_AUDIO_LLM if media_type == "audio" else DEFAULT_MEDIA_LLM
    llm_spec = llm or default
    provider, model = parse_external_llm(llm_spec)
    api_key = require_api_key(provider, settings)
    return provider, model, api_key


def resolve_document_analysis_llm(llm: str | None, settings: Settings) -> tuple[str, str, str]:
    """Resolve provider, model, and API key for the document `--analyze` text pass."""
    llm_spec = llm or DEFAULT_DOCUMENT_ANALYSIS_LLM
    provider, model = parse_external_llm(llm_spec)
    api_key = require_api_key(provider, settings)
    return provider, model, api_key


def resolve_companion_llm(companion_llm: str | None, llm: str | None, settings: Settings) -> tuple[str, str, str]:
    """Resolve provider, model, and API key for companion context analysis.

    Precedence: --companion-llm > --llm > DEFAULT_COMPANION_LLM.
    The default is a multimodal model because companion notes commonly
    reference screenshots that are sent inline with the analysis call.
    """
    llm_spec = companion_llm or llm or DEFAULT_COMPANION_LLM
    provider, model = parse_external_llm(llm_spec)
    api_key = require_api_key(provider, settings)
    return provider, model, api_key


def resolve_pptx_slide_llm(slide_vlm: str | None, llm: str | None) -> str:
    """Resolve the provider/model slug for PPTX whole-slide screenshot analysis.

    Precedence: --slide-vlm > --llm > DEFAULT_PPTX_SLIDE_VLM. This mirrors the
    --media-llm > --llm > default pattern used for audio/video.
    """
    return slide_vlm or llm or DEFAULT_PPTX_SLIDE_VLM


def resolve_image_llm(llm: str | None) -> str:
    """Resolve the provider/model slug for image conversion.

    Precedence: explicit --llm > DEFAULT_IMAGE_LLM.
    """
    return llm or DEFAULT_IMAGE_LLM
