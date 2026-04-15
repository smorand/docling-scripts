"""Application settings loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Configuration for doc-convert.

    Environment variables (no prefix, standard names):
        GEMINI_API_KEY       - Gemini API key for VLM conversion
        GOOGLE_CREDENTIALS   - Path to Google credentials JSON
    """

    gemini_api_key: str = ""
    google_credentials: str = ""
    gemini_model: str = "gemini-3-flash-preview"
    gemini_max_tokens: int = 8192
    gemini_timeout: float = 120.0
    gemini_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
