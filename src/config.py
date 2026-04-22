"""Application settings loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

DEFAULT_MODELS_PATH = Path.home() / ".cache" / "models"


class Settings(BaseSettings):
    """Configuration for doc-convert.

    Environment variables (no prefix, standard names):
        MODELS_PATH          - Local model cache directory (default: ~/.cache/models)
        GOOGLE_API_KEY       - Google GenAI API key (for google/ provider)
        OPENROUTER_API_KEY   - OpenRouter API key (for openrouter/ provider)
        GOOGLE_CREDENTIALS   - Path to Google credentials JSON (for Google Docs/Sheets)
    """

    models_path: str = str(DEFAULT_MODELS_PATH)
    google_api_key: str = ""
    openrouter_api_key: str = ""
    google_credentials: str = ""
    notes_api_url: str = "https://notes.mcp.scm-platform.org"
    llm_max_tokens: int = 8192
    llm_timeout: float = 120.0
