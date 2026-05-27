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

# Static URLs for providers with a fixed endpoint. `ibm` is dynamic, resolved
# at runtime from settings.ibm_ica_base_url. Use get_provider_url() to read.
PROVIDER_URLS: dict[str, str] = {
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}

SUPPORTED_PROVIDERS: tuple[str, ...] = ("google", "openrouter", "ibm")

DEFAULT_MEDIA_LLM = "ibm/gemini-3-pro-preview"


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


def resolve_media_llm(use_external_llm: str | None, settings: Settings) -> tuple[str, str, str]:
    """Resolve provider, model, and API key for audio/video processing."""
    llm_spec = use_external_llm or DEFAULT_MEDIA_LLM
    provider, model = parse_external_llm(llm_spec)
    api_key = require_api_key(provider, settings)
    return provider, model, api_key
