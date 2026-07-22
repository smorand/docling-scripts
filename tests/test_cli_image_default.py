from __future__ import annotations

from pathlib import Path
from unittest import mock

from docling.datamodel.base_models import InputFormat
from typer.testing import CliRunner

from doc_convert.cli import app
from doc_convert.formats import CaptionsOff, Engine, OcrOff

runner = CliRunner()


def test_image_input_defaults_llm_to_ibm_haiku(tmp_path: Path) -> None:
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")

    captured: dict[str, object] = {}

    class _FakeImageConverter:
        def __init__(self, source: Path, options: object) -> None:
            captured["source"] = source
            captured["options"] = options

        def convert(self) -> None:
            return None

        def run_analysis(self, *_args: object, **_kwargs: object) -> bool:
            return False

    with (
        mock.patch("doc_convert.formats.detect_format", return_value=InputFormat.IMAGE),
        mock.patch("doc_convert.google_docs.is_google_url", return_value=False),
        mock.patch("doc_convert.companion.load_companion_context", return_value=None),
        mock.patch("doc_convert.output.make_document_symlink", return_value=None),
        mock.patch("doc_convert.output.check_step_cache", return_value=False),
        mock.patch("doc_convert.cli.resolve_captions", return_value=CaptionsOff()),
        mock.patch("doc_convert.cli.resolve_ocr_model", return_value=OcrOff()),
        mock.patch("doc_convert.converters.image.ImageConverter", _FakeImageConverter),
    ):
        result = runner.invoke(app, [str(image_path)])

    assert result.exit_code == 0
    options = captured["options"]
    assert options.llm == "ibm/claude-haiku-4-5"
    assert options.engine == Engine.LOCAL


def test_image_input_keeps_explicit_llm(tmp_path: Path) -> None:
    image_path = tmp_path / "scan.png"
    image_path.write_bytes(b"fake")

    captured: dict[str, object] = {}

    class _FakeImageConverter:
        def __init__(self, source: Path, options: object) -> None:
            captured["options"] = options

        def convert(self) -> None:
            return None

        def run_analysis(self, *_args: object, **_kwargs: object) -> bool:
            return False

    with (
        mock.patch("doc_convert.formats.detect_format", return_value=InputFormat.IMAGE),
        mock.patch("doc_convert.google_docs.is_google_url", return_value=False),
        mock.patch("doc_convert.companion.load_companion_context", return_value=None),
        mock.patch("doc_convert.output.make_document_symlink", return_value=None),
        mock.patch("doc_convert.output.check_step_cache", return_value=False),
        mock.patch("doc_convert.cli.resolve_captions", return_value=CaptionsOff()),
        mock.patch("doc_convert.cli.resolve_ocr_model", return_value=OcrOff()),
        mock.patch("doc_convert.converters.image.ImageConverter", _FakeImageConverter),
    ):
        result = runner.invoke(app, [str(image_path), "--llm", "google/gemini-3.1-pro-preview"])

    assert result.exit_code == 0
    options = captured["options"]
    assert options.llm == "google/gemini-3.1-pro-preview"
