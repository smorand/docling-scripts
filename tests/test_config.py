"""Tests for configuration loading."""

import pytest

from config import Settings


def test_default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings should load with defaults when no DOC_CONVERT_ env vars are set."""
    # Ensure no DOC_CONVERT_ variables leak into the test
    for key in list(Settings.model_fields.keys()):
        monkeypatch.delenv(f"DOC_CONVERT_{key.upper()}", raising=False)

    settings = Settings()
    assert settings.google_api_key == ""
    assert settings.openrouter_api_key == ""
    assert settings.ibm_ica_model_key == ""
    assert settings.ibm_ica_base_url == ""
    assert settings.google_credentials == ""
    assert settings.google_client_id == ""
    assert settings.google_client_secret == ""
    assert settings.notes_api_url == "https://notes.mcp.scm-platform.org"
    assert settings.llm_max_tokens == 16384
    assert settings.llm_timeout == 120.0
    assert "models" in settings.models_path


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings should read from DOC_CONVERT_ prefixed environment variables."""
    monkeypatch.setenv("DOC_CONVERT_GOOGLE_API_KEY", "test-key")
    monkeypatch.setenv("DOC_CONVERT_OPENROUTER_API_KEY", "test-or-key")
    monkeypatch.setenv("DOC_CONVERT_IBM_ICA_MODEL_KEY", "test-ibm-key")
    monkeypatch.setenv("DOC_CONVERT_IBM_ICA_BASE_URL", "https://example.test/ica/v1")
    monkeypatch.setenv("DOC_CONVERT_GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("DOC_CONVERT_GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("DOC_CONVERT_LLM_MAX_TOKENS", "8192")
    monkeypatch.setenv("DOC_CONVERT_LLM_TIMEOUT", "60.0")

    settings = Settings()
    assert settings.google_api_key == "test-key"
    assert settings.openrouter_api_key == "test-or-key"
    assert settings.ibm_ica_model_key == "test-ibm-key"
    assert settings.ibm_ica_base_url == "https://example.test/ica/v1"
    assert settings.google_client_id == "test-client-id"
    assert settings.google_client_secret == "test-client-secret"
    assert settings.llm_max_tokens == 8192
    assert settings.llm_timeout == 60.0


def test_settings_ignores_unprefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings should ignore unprefixed environment variables."""
    for key in list(Settings.model_fields.keys()):
        monkeypatch.delenv(f"DOC_CONVERT_{key.upper()}", raising=False)

    monkeypatch.setenv("GOOGLE_API_KEY", "legacy-key")
    monkeypatch.setenv("IBM_ICA_MODEL_KEY", "legacy-ibm-key")

    settings = Settings()
    assert settings.google_api_key == ""
    assert settings.ibm_ica_model_key == ""
