"""Tests for external LLM provider parsing and resolution."""

from __future__ import annotations

import pytest
import typer

from config import Settings
from doc_convert.providers import (
    DEFAULT_AUDIO_LLM,
    DEFAULT_DOCUMENT_ANALYSIS_LLM,
    DEFAULT_IMAGE_LLM,
    DEFAULT_MEDIA_LLM,
    DEFAULT_PPTX_SLIDE_VLM,
    build_context_block,
    get_caption_prompt,
    get_meeting_summary_prompt,
    get_provider_url,
    get_table_prompt,
    parse_external_llm,
    require_api_key,
    resolve_document_analysis_llm,
    resolve_image_llm,
    resolve_media_llm,
    resolve_pptx_slide_llm,
)


def test_parse_external_llm_simple() -> None:
    assert parse_external_llm("google/gemini-3.1-pro-preview") == ("google", "gemini-3.1-pro-preview")
    assert parse_external_llm("ibm/claude-opus-4-8") == ("ibm", "claude-opus-4-8")


def test_parse_external_llm_nested_model() -> None:
    # openrouter models keep their own provider/model slug after the prefix
    assert parse_external_llm("openrouter/google/gemini-3-pro") == ("openrouter", "google/gemini-3-pro")


def test_parse_external_llm_missing_model() -> None:
    with pytest.raises(typer.Exit):
        parse_external_llm("google/")


def test_parse_external_llm_unknown_provider() -> None:
    with pytest.raises(typer.Exit):
        parse_external_llm("acme/model")


def test_get_provider_url_static() -> None:
    settings = Settings(ibm_ica_base_url="")
    assert "generativelanguage" in get_provider_url("google", settings)
    assert "openrouter.ai" in get_provider_url("openrouter", settings)


def test_get_provider_url_ibm_dynamic(ibm_settings: Settings) -> None:
    url = get_provider_url("ibm", ibm_settings)
    assert url == "https://ica.example.test/v1/chat/completions"


def test_get_provider_url_ibm_missing_base(empty_settings: Settings) -> None:
    with pytest.raises(typer.Exit):
        get_provider_url("ibm", empty_settings)


def test_get_provider_url_unknown(empty_settings: Settings) -> None:
    with pytest.raises(typer.Exit):
        get_provider_url("acme", empty_settings)


def test_require_api_key_present(google_settings: Settings, ibm_settings: Settings) -> None:
    assert require_api_key("google", google_settings) == "g-key"
    assert require_api_key("ibm", ibm_settings) == "ibm-key"


def test_require_api_key_missing(empty_settings: Settings) -> None:
    for provider in ("google", "openrouter", "ibm"):
        with pytest.raises(typer.Exit):
            require_api_key(provider, empty_settings)


def test_require_api_key_unknown_provider(google_settings: Settings) -> None:
    with pytest.raises(typer.Exit):
        require_api_key("acme", google_settings)


def test_resolve_media_llm_default(google_settings: Settings) -> None:
    provider, model, key = resolve_media_llm(None, google_settings)
    assert (provider, key) == ("google", "g-key")
    assert model == DEFAULT_MEDIA_LLM.split("/", 1)[1]


def test_resolve_media_llm_override(ibm_settings: Settings) -> None:
    provider, model, key = resolve_media_llm("ibm/claude-opus-4-8", ibm_settings)
    assert (provider, model, key) == ("ibm", "claude-opus-4-8", "ibm-key")


def test_resolve_media_llm_audio_default_is_ibm(ibm_settings: Settings) -> None:
    provider, model, key = resolve_media_llm(None, ibm_settings, media_type="audio")
    assert (provider, key) == ("ibm", "ibm-key")
    assert model == DEFAULT_AUDIO_LLM.split("/", 1)[1]


def test_resolve_media_llm_video_default_is_google(google_settings: Settings) -> None:
    provider, _, _ = resolve_media_llm(None, google_settings, media_type="video")
    assert provider == "google"


def test_resolve_document_analysis_llm_default(ibm_settings: Settings) -> None:
    provider, model, key = resolve_document_analysis_llm(None, ibm_settings)
    assert provider == DEFAULT_DOCUMENT_ANALYSIS_LLM.split("/", 1)[0]
    assert model == DEFAULT_DOCUMENT_ANALYSIS_LLM.split("/", 1)[1]
    assert key == "ibm-key"


def test_build_context_block_empty() -> None:
    assert build_context_block() == ""
    assert build_context_block("", "") == ""


def test_build_context_block_caption_only() -> None:
    block = build_context_block(caption="A diagram")
    assert "Document caption: A diagram" in block
    assert block.endswith("\n\n")


def test_build_context_block_both() -> None:
    block = build_context_block(caption="Cap", mention="see fig 1")
    assert "Document caption: Cap" in block
    assert "Mentioned in document: see fig 1" in block


def test_prompt_getters_return_nonempty() -> None:
    assert get_caption_prompt().strip()
    assert get_table_prompt().strip()
    # meeting summary template embeds the CSS block
    assert "<style>" not in get_meeting_summary_prompt() or "body {" in get_meeting_summary_prompt()


# ── --slide-vlm resolution (PPTX whole-slide screenshot analysis) ─────────


def test_resolve_pptx_slide_llm_explicit_wins() -> None:
    assert resolve_pptx_slide_llm("ibm/claude-opus-4-8", "google/gemini-3.1-pro-preview") == "ibm/claude-opus-4-8"


def test_resolve_pptx_slide_llm_falls_back_to_llm() -> None:
    assert (
        resolve_pptx_slide_llm(None, "openrouter/anthropic/claude-haiku-4.5") == "openrouter/anthropic/claude-haiku-4.5"
    )


def test_resolve_pptx_slide_llm_default() -> None:
    assert resolve_pptx_slide_llm(None, None) == DEFAULT_PPTX_SLIDE_VLM
    assert DEFAULT_PPTX_SLIDE_VLM == "ibm/claude-sonnet-4-6"


def test_resolve_image_llm_uses_explicit_value() -> None:
    assert resolve_image_llm("google/gemini-3.1-pro-preview") == "google/gemini-3.1-pro-preview"


def test_resolve_image_llm_default() -> None:
    assert resolve_image_llm(None) == DEFAULT_IMAGE_LLM
    assert DEFAULT_IMAGE_LLM == "ibm/claude-haiku-4-5"
