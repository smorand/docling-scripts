"""Tests for configuration loading."""

import os

from config import Settings


def test_default_settings() -> None:
    """Settings should load with defaults when no env vars are set."""
    settings = Settings(google_api_key="", openrouter_api_key="", google_credentials="")
    assert settings.google_api_key == ""
    assert settings.openrouter_api_key == ""
    assert settings.google_credentials == ""
    assert settings.llm_max_tokens == 8192
    assert settings.llm_timeout == 120.0
    assert "models" in settings.models_path


def test_settings_from_env(monkeypatch: object) -> None:
    """Settings should read from environment variables."""
    os.environ["GOOGLE_API_KEY"] = "test-key"
    os.environ["OPENROUTER_API_KEY"] = "test-or-key"
    try:
        settings = Settings()
        assert settings.google_api_key == "test-key"
        assert settings.openrouter_api_key == "test-or-key"
    finally:
        del os.environ["GOOGLE_API_KEY"]
        del os.environ["OPENROUTER_API_KEY"]
