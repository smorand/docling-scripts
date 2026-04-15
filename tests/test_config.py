"""Tests for configuration loading."""

import os

from config import Settings


def test_default_settings() -> None:
    """Settings should load with defaults when no env vars are set."""
    settings = Settings(gemini_api_key="", google_credentials="")
    assert settings.gemini_api_key == ""
    assert settings.google_credentials == ""
    assert settings.gemini_model == "gemini-3-flash-preview"
    assert settings.gemini_max_tokens == 8192
    assert settings.gemini_timeout == 120.0


def test_settings_from_env(monkeypatch: object) -> None:
    """Settings should read from environment variables."""
    os.environ["GEMINI_API_KEY"] = "test-key"
    try:
        settings = Settings()
        assert settings.gemini_api_key == "test-key"
    finally:
        del os.environ["GEMINI_API_KEY"]
