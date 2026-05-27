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
        IBM_ICA_MODEL_KEY    - IBM ICA API key (for ibm/ provider, OpenAI-compatible)
        IBM_ICA_BASE_URL     - IBM ICA base URL (e.g. https://api.nextgen-beta.ica.ibm.com/ica/v1)
        GOOGLE_CREDENTIALS   - Path to Google credentials JSON (for Google Docs/Sheets)
    """

    models_path: str = str(DEFAULT_MODELS_PATH)
    google_api_key: str = ""
    openrouter_api_key: str = ""
    ibm_ica_model_key: str = ""
    ibm_ica_base_url: str = ""
    google_credentials: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    notes_api_url: str = "https://notes.mcp.scm-platform.org"
    llm_max_tokens: int = 8192
    llm_timeout: float = 120.0
