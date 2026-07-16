"""Tests for PPTX whole-slide screenshot rendering, notes extraction, and VLM analysis."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import typer
from pptx import Presentation

from doc_convert.pptx_slide_vlm import (
    DEFAULT_PPTX_SLIDE_VLM,
    _normalize_heading_levels,
    _pptx_to_pdf,
    extract_hidden_slide_numbers,
    extract_slide_notes,
)


def _make_pptx(tmp_path: Path, *, notes: dict[int, str]) -> Path:
    prs = Presentation()
    layout = prs.slide_layouts[6]  # blank layout
    slide_count = max(notes.keys(), default=1)
    for i in range(1, slide_count + 1):
        slide = prs.slides.add_slide(layout)
        if i in notes:
            slide.notes_slide.notes_text_frame.text = notes[i]
    path = tmp_path / "deck.pptx"
    prs.save(str(path))
    return path


def test_default_model_is_confirmed_ibm_sonnet() -> None:
    # Confirmed present via IBM ICA's GET /models (see providers.DEFAULT_PPTX_SLIDE_VLM).
    assert DEFAULT_PPTX_SLIDE_VLM == "ibm/claude-sonnet-4-6"


def test_extract_slide_notes_returns_only_nonempty(tmp_path: Path) -> None:
    pptx_path = _make_pptx(tmp_path, notes={1: "Remember to mention the roadmap", 3: "  "})
    notes = extract_slide_notes(pptx_path)
    assert notes == {1: "Remember to mention the roadmap"}


def test_extract_hidden_slide_numbers_marks_hidden_only(tmp_path: Path) -> None:
    prs = Presentation()
    layout = prs.slide_layouts[6]
    for _ in range(3):
        prs.slides.add_slide(layout)
    # python-pptx has no high-level API for the hidden flag; set the raw XML
    # attribute the same way PowerPoint does (<p:sld show="0">).
    prs.slides[1]._element.set("show", "0")
    path = tmp_path / "deck.pptx"
    prs.save(str(path))

    assert extract_hidden_slide_numbers(path) == {2}


def test_extract_hidden_slide_numbers_none_hidden(tmp_path: Path) -> None:
    pptx_path = _make_pptx(tmp_path, notes={})
    assert extract_hidden_slide_numbers(pptx_path) == set()


def test_pptx_to_pdf_forces_export_hidden_slides(tmp_path: Path) -> None:
    """LibreOffice must be told to keep hidden slides in the PDF: otherwise PDF
    page numbers drift out of sync with docling/python-pptx slide numbering,
    misaligning every subsequent slide's text/visual/notes sections.
    """
    pptx_path = tmp_path / "deck.pptx"
    pptx_path.write_bytes(b"fake")
    captured_cmd: list[str] = []

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd: list[str], **_kwargs: object) -> _Result:
        captured_cmd.extend(cmd)
        (tmp_path / f"{pptx_path.stem}.pdf").write_bytes(b"%PDF-1.4 fake")
        return _Result()

    with patch("shutil.which", return_value="/usr/bin/soffice"), patch("subprocess.run", side_effect=fake_run):
        _pptx_to_pdf(pptx_path, tmp_path)

    convert_arg = captured_cmd[captured_cmd.index("--convert-to") + 1]
    assert "ExportHiddenSlides" in convert_arg
    assert '"value":"true"' in convert_arg


def test_extract_slide_notes_no_notes_slide(tmp_path: Path) -> None:
    pptx_path = _make_pptx(tmp_path, notes={})
    assert extract_slide_notes(pptx_path) == {}


def test_pptx_to_pdf_missing_soffice_exits(tmp_path: Path) -> None:
    with patch("shutil.which", return_value=None), pytest.raises(typer.Exit):
        _pptx_to_pdf(tmp_path / "deck.pptx", tmp_path)


# ── heading-level safety net (VLM sometimes emits its own top-level heading) ───


def test_normalize_heading_levels_demotes_h2() -> None:
    text = "## Slide Analysis\n\nSome content."
    out = _normalize_heading_levels(text)
    assert out.startswith("#### Slide Analysis")
    assert "## Slide Analysis" not in out.replace("#### Slide Analysis", "")


def test_normalize_heading_levels_leaves_deep_headings_alone() -> None:
    text = "#### Purpose\n\nContent\n\n##### Sub-point"
    assert _normalize_heading_levels(text) == text


def test_normalize_heading_levels_no_heading_is_noop() -> None:
    text = "Just plain text, no heading markers at all."
    assert _normalize_heading_levels(text) == text


def test_normalize_heading_levels_multiple_offending_lines() -> None:
    text = "# Title\n\nIntro.\n\n## Section\n\nBody."
    out = _normalize_heading_levels(text)
    assert out.startswith("#### Title\n")
    assert "#### Section" in out
    assert not out.splitlines()[0].startswith("# ")
    assert "\n## Section" not in out
