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
    "Describe this image in rich detail for an image catalog. Cover, in order:\n\n"
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


def get_caption_prompt() -> str:
    """Prompt used to describe a single extracted figure (for images.md)."""
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("document", "caption_prompt", _DEFAULT_CAPTION_PROMPT)


# Static URLs for providers with a fixed endpoint. `ibm` is dynamic, resolved
# at runtime from settings.ibm_ica_base_url. Use get_provider_url() to read.
PROVIDER_URLS: dict[str, str] = {
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}

SUPPORTED_PROVIDERS: tuple[str, ...] = ("google", "openrouter", "ibm")

# Default uses google/ (Gemini Files API: upload → generate), which has no
# payload size limit. Inline-base64 providers (ibm/, openrouter/) fail with 502
# on large audio/video recordings. gemini-3-pro-preview was retired on the Google
# API ("no longer available"); gemini-3.1-pro-preview is its current successor.
DEFAULT_MEDIA_LLM = "google/gemini-3.1-pro-preview"

# Default model for the `--analyze` text-only analysis pass on documents.
# Picked for reasoning quality on long markdown context. Document analysis only
# sends text, so any provider works (no Files API constraint).
DEFAULT_DOCUMENT_ANALYSIS_LLM = "ibm/claude-opus-4-8"


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
        google/gemini-3-flash-preview -> ("google", "gemini-3-flash-preview")
        openrouter/google/gemini-3-pro-preview -> ("openrouter", "google/gemini-3-pro-preview")
        ibm/gemini-3-pro-preview -> ("ibm", "gemini-3-pro-preview")
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


def resolve_media_llm(llm: str | None, settings: Settings) -> tuple[str, str, str]:
    """Resolve provider, model, and API key for audio/video processing."""
    llm_spec = llm or DEFAULT_MEDIA_LLM
    provider, model = parse_external_llm(llm_spec)
    api_key = require_api_key(provider, settings)
    return provider, model, api_key


def resolve_document_analysis_llm(llm: str | None, settings: Settings) -> tuple[str, str, str]:
    """Resolve provider, model, and API key for the document `--analyze` text pass."""
    llm_spec = llm or DEFAULT_DOCUMENT_ANALYSIS_LLM
    provider, model = parse_external_llm(llm_spec)
    api_key = require_api_key(provider, settings)
    return provider, model, api_key
