"""Application settings loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODELS_PATH = Path.home() / ".cache" / "models"


class Settings(BaseSettings):
    """Configuration for doc-convert.

    Environment variables (prefixed with DOC_CONVERT_):
        DOC_CONVERT_MODELS_PATH          - Local model cache directory (default: ~/.cache/models)
        DOC_CONVERT_GOOGLE_API_KEY       - Google GenAI API key (for google/ provider)
        DOC_CONVERT_OPENROUTER_API_KEY   - OpenRouter API key (for openrouter/ provider)
        DOC_CONVERT_IBM_ICA_MODEL_KEY    - IBM ICA API key (for ibm/ provider, OpenAI-compatible)
        DOC_CONVERT_IBM_ICA_BASE_URL     - IBM ICA base URL (e.g. https://api.servicesessentials.ibm.com/v1)
        DEVPASS_API_KEY                  - DevPass LLM gateway API key (for devpass/ provider)
        DEVPASS_BASE_URL                 - DevPass base URL (default: https://api.llmgateway.io/v1)
        DOC_CONVERT_GOOGLE_CREDENTIALS   - Path to Google credentials JSON (for Google Docs/Sheets)
        DOC_CONVERT_GOOGLE_CLIENT_ID     - Google OAuth client ID (for --note)
        DOC_CONVERT_GOOGLE_CLIENT_SECRET - Google OAuth client secret (for --note)
        DOC_CONVERT_NOTES_API_URL        - Notes API base URL (default: https://notes.mcp.scm-platform.org)
        DOC_CONVERT_LLM_MAX_TOKENS       - Maximum tokens for LLM generation (default: 16384)
        DOC_CONVERT_LLM_TIMEOUT          - Timeout in seconds for LLM HTTP calls (default: 120.0)
    """

    model_config = SettingsConfigDict(env_prefix="DOC_CONVERT_", extra="ignore", populate_by_name=True)

    models_path: str = str(DEFAULT_MODELS_PATH)
    google_api_key: str = ""
    openrouter_api_key: str = ""
    ibm_ica_model_key: str = ""
    ibm_ica_base_url: str = ""
    # devpass vars have no DOC_CONVERT_ prefix: read DEVPASS_* directly.
    devpass_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("DEVPASS_API_KEY", "DOC_CONVERT_DEVPASS_API_KEY"),
    )
    devpass_base_url: str = Field(
        default="https://api.llmgateway.io/v1",
        validation_alias=AliasChoices("DEVPASS_BASE_URL", "DOC_CONVERT_DEVPASS_BASE_URL"),
    )
    google_credentials: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    notes_api_url: str = "https://notes.mcp.scm-platform.org"
    llm_max_tokens: int = 16384
    llm_timeout: float = 120.0
