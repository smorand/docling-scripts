"""Tests for pure markdown-building helpers."""

from __future__ import annotations

from pathlib import Path

from doc_convert.markdown import (
    _extract_label,
    _find_mention,
    _format_description_block,
    _heading_for,
    _shorten_sentence,
    get_pdf_metadata,
)


def test_extract_label() -> None:
    assert _extract_label("Figure 3: Architecture diagram") == "Figure 3"
    assert _extract_label("fig. 2 overview") == "Figure 2"
    assert _extract_label("Table 1") == "Table 1"
    assert _extract_label("no marker here") == ""
    assert _extract_label("") == ""


def test_shorten_sentence() -> None:
    assert _shorten_sentence("  a   b\tc ") == "a b c"
    long = "word " * 100
    out = _shorten_sentence(long, max_chars=20)
    assert len(out) <= 20
    assert out.endswith("…")


def test_find_mention() -> None:
    body = ["Intro text.", "As shown in Figure 2, the trend rises.", "Other."]
    assert "Figure 2" in _find_mention(body, "2", "Figure")
    assert _find_mention(body, "9", "Figure") == ""


def test_heading_for() -> None:
    assert _heading_for("Figure 1", "Figure", "Figure 1: Layout") == "#### Figure 1: Layout"
    assert _heading_for("Figure 1", "Figure", "") == "#### Figure 1"
    assert _heading_for("", "Figure", "Caption only") == "#### Figure: Caption only"
    assert _heading_for("", "Figure", "") == "#### Figure"


def test_format_description_block_empty() -> None:
    assert _format_description_block("", "") == ""


def test_format_description_block_description_only() -> None:
    block = _format_description_block("A chart\nshowing growth", "")
    assert block == "> A chart\n> showing growth"


def test_format_description_block_with_mention() -> None:
    block = _format_description_block("Desc", "Figure 1 shows X")
    assert "> Desc" in block
    assert "Cited in document" in block


def test_get_pdf_metadata_invalid_file(tmp_path: Path) -> None:
    fake = tmp_path / "not-really.pdf"
    fake.write_text("not a pdf")
    meta = get_pdf_metadata(str(fake))
    # Gracefully degrades: at least the filename is recorded.
    assert meta["File"] == "not-really.pdf"
