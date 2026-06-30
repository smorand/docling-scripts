"""Tests for format detection, captions spec, and engine selection."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from docling.datamodel.base_models import InputFormat

from config import Settings
from doc_convert.converters.eml import _html_to_markdown
from doc_convert.formats import (
    CaptionsLlm,
    CaptionsLocal,
    CaptionsOff,
    Engine,
    OcrLlm,
    OcrLocal,
    OcrOff,
    detect_format,
    parse_captions,
    parse_ocr_model,
    resolve_captions,
    resolve_ocr_model,
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


# ── --ocr-model parsing ───────────────────────────────────────────────────


def test_parse_ocr_model_off() -> None:
    assert parse_ocr_model("off") == OcrOff()


def test_parse_ocr_model_local_aliases() -> None:
    assert parse_ocr_model("local") == OcrLocal("tesseract")
    assert parse_ocr_model("tesseract") == OcrLocal("tesseract")


def test_parse_ocr_model_llm_slug() -> None:
    assert parse_ocr_model("ibm/claude-haiku-4-5") == OcrLlm("ibm", "claude-haiku-4-5")


def test_parse_ocr_model_invalid() -> None:
    with pytest.raises(typer.Exit):
        parse_ocr_model("not-an-engine")


def test_resolve_ocr_model_default_is_gemini() -> None:
    assert resolve_ocr_model(None, no_ocr=False) == OcrLlm("google", "gemini-3.1-pro-preview")


def test_resolve_ocr_model_local_uses_tesseract() -> None:
    assert resolve_ocr_model("local", no_ocr=False) == OcrLocal("tesseract")


def test_resolve_ocr_model_no_ocr_wins() -> None:
    # --no-ocr overrides any explicit --ocr-model value.
    assert resolve_ocr_model("ibm/claude-haiku-4-5", no_ocr=True) == OcrOff()
    assert resolve_ocr_model(None, no_ocr=True) == OcrOff()


def test_resolve_ocr_model_explicit_llm() -> None:
    assert resolve_ocr_model("ibm/claude-haiku-4-5", no_ocr=False) == OcrLlm("ibm", "claude-haiku-4-5")


# ── email HTML → markdown ─────────────────────────────────────────────────


def test_html_to_markdown_flattens_layout_tables_and_keeps_links() -> None:
    # Marketing/HR emails wrap real content in nested layout tables. Docling
    # collapsed all of this into one giant table cell and dropped link URLs;
    # the html2text path must yield readable paragraphs with links intact.
    html = """
    <table><tr><td>
      <table><tr><td>
        <p>Chers IBMers,</p>
        <p>Voir la <a href="https://example.com/proc">procédure</a> ici.</p>
      </td></tr></table>
    </td></tr></table>
    """
    md = _html_to_markdown(html)

    assert "Chers IBMers," in md
    assert "[procédure](https://example.com/proc)" in md
    assert "| --- |" not in md  # no markdown table skeleton
    assert "\n" in md  # broken into paragraphs, not one line


def test_html_to_markdown_strips_tracking_and_spacer_images() -> None:
    html = """
    <p>Bonjour</p>
    <img src="https://cdn.example.com/IMAGE-PLACEHOLDER-ONLY-SPACER-1x1.gif">
    <img src="https://track.example.com/trk?t=1&mid=abc" width="1" height="1">
    <a href="https://track.example.com/open?id=42"></a>
    """
    md = _html_to_markdown(html)

    assert "Bonjour" in md
    assert "trk?" not in md
    assert "spacer" not in md.lower()
    assert "[ ]" not in md  # empty tracking anchors removed


def test_html_to_markdown_keeps_data_table_inside_layout_table() -> None:
    # A genuine data table (header cells) nested in a presentation/layout table
    # must survive as a real markdown table; the layout wrapper is unwrapped.
    html = """
    <table role="presentation"><tr><td>
      <p>Barème :</p>
      <table>
        <tr><th>Statut</th><th>Durée</th></tr>
        <tr><td>Cadre</td><td>180j</td></tr>
        <tr><td>Non-cadre</td><td>90j</td></tr>
      </table>
    </td></tr></table>
    """
    md = _html_to_markdown(html)

    assert "Barème :" in md
    assert "| Statut" in md  # rendered as a markdown table
    assert "| --- |" in md or "|---" in md or "|--" in md  # header separator row
    assert "Cadre" in md and "180j" in md


def test_html_to_markdown_escaped_dashes_become_tight_bullets() -> None:
    html = """
    <p>Contacts :</p>
    <p>- Médecins du travail</p>
    <p>- Assistantes sociales</p>
    <p>- Ligne d'écoute</p>
    """
    md = _html_to_markdown(html)

    assert "\\-" not in md  # no escaped dashes left
    assert "- Médecins du travail" in md
    # consecutive bullets are tight (no blank line between them)
    assert "- Médecins du travail\n- Assistantes sociales" in md
