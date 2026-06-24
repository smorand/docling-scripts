"""Tests for format detection, captions spec, and engine selection."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from docling.datamodel.base_models import InputFormat

from config import Settings
from doc_convert.formats import (
    CaptionsLlm,
    CaptionsLocal,
    CaptionsOff,
    Engine,
    detect_format,
    parse_captions,
    resolve_captions,
)


def test_detect_format_known() -> None:
    assert detect_format(Path("a.pdf")) is InputFormat.PDF
    assert detect_format(Path("a.PNG")) is InputFormat.IMAGE
    assert detect_format(Path("deck.pptm")) is InputFormat.PPTX


def test_detect_format_unknown() -> None:
    with pytest.raises(typer.Exit):
        detect_format(Path("archive.zip"))


def test_engine_values() -> None:
    assert Engine.LOCAL.value == "local"
    assert Engine.LLM.value == "llm"


def test_parse_captions_off() -> None:
    assert parse_captions("off") == CaptionsOff()


def test_parse_captions_local_preset() -> None:
    assert parse_captions("smolvlm") == CaptionsLocal("smolvlm")


def test_parse_captions_llm_slug() -> None:
    assert parse_captions("google/gemini-3.1-flash") == CaptionsLlm("google", "gemini-3.1-flash")


def test_parse_captions_invalid() -> None:
    with pytest.raises(typer.Exit):
        parse_captions("not-a-preset")


def test_resolve_captions_explicit_wins(google_settings: Settings) -> None:
    spec = resolve_captions("off", "google/gemini-3.1-pro", google_settings)
    assert spec == CaptionsOff()


def test_resolve_captions_llm_fallback(empty_settings: Settings) -> None:
    spec = resolve_captions(None, "ibm/claude-haiku-4-5", empty_settings)
    assert spec == CaptionsLlm("ibm", "claude-haiku-4-5")


def test_resolve_captions_auto_prefers_credentialed_cloud(google_settings: Settings) -> None:
    # No --captions, no --llm: first cloud preference with creds is picked.
    spec = resolve_captions(None, None, google_settings)
    assert spec == CaptionsLlm("google", "gemini-3.1-flash-lite-preview")


def test_resolve_captions_local_default_when_no_creds(empty_settings: Settings) -> None:
    spec = resolve_captions(None, None, empty_settings)
    assert spec == CaptionsLocal("smolvlm")
