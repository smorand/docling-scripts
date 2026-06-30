"""Tests for the image/PDF-via-LLM converter."""

from __future__ import annotations

from pathlib import Path

import pytest
import typer

from doc_convert.base import ConvertOptions
from doc_convert.converters.image import ImageConverter


def test_convert_raises_on_empty_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty VLM response (e.g. a retired/404 model) must fail loudly.

    Regression: a blank export used to be written as a 0-byte document.md with
    exit 0, hiding the API error. The converter must raise and write nothing.
    """
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0")  # minimal JPEG-ish bytes; content is mocked away
    out = tmp_path / "photo_docling"
    options = ConvertOptions(output_dir=out, llm="google/gemini-3.1-pro-preview")

    monkeypatch.setattr(
        "doc_convert.converters.image.convert_image_to_markdown",
        lambda *args, **kwargs: "   \n  ",  # whitespace-only counts as empty
    )

    converter = ImageConverter(src, options)
    with pytest.raises(typer.Exit):
        converter.convert()

    assert not (out / "document.md").exists()


def test_convert_writes_markdown_when_non_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-empty VLM response is written to document.md as before."""
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0")
    out = tmp_path / "photo_docling"
    options = ConvertOptions(output_dir=out, llm="google/gemini-3.1-pro-preview")

    monkeypatch.setattr(
        "doc_convert.converters.image.convert_image_to_markdown",
        lambda *args, **kwargs: "# Hello\n\nReal extracted text.",
    )

    converter = ImageConverter(src, options)
    converter.convert()

    assert (out / "document.md").read_text() == "# Hello\n\nReal extracted text."
