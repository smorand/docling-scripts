"""Shared test fixtures."""

from __future__ import annotations

import pytest

from config import Settings


@pytest.fixture
def empty_settings() -> Settings:
    """Settings with no provider credentials configured."""
    return Settings(
        google_api_key="",
        openrouter_api_key="",
        ibm_ica_model_key="",
        ibm_ica_base_url="",
        google_credentials="",
    )


@pytest.fixture
def google_settings() -> Settings:
    """Settings with only a Google API key."""
    return Settings(
        google_api_key="g-key",
        openrouter_api_key="",
        ibm_ica_model_key="",
        ibm_ica_base_url="",
        google_credentials="",
    )


@pytest.fixture
def ibm_settings() -> Settings:
    """Settings with IBM ICA credentials configured."""
    return Settings(
        google_api_key="",
        openrouter_api_key="",
        ibm_ica_model_key="ibm-key",
        ibm_ica_base_url="https://ica.example.test/v1",
        google_credentials="",
    )
