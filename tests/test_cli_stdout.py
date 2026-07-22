"""Tests for the --stdout flag: print the resulting document.md on stdout."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from doc_convert.cli import _print_document_stdout, app

runner = CliRunner()


def _make_xlsx(path: Path) -> Path:
    openpyxl = __import__("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "hello"
    ws["B1"] = "world"
    wb.save(path)
    return path


def test_stdout_prints_document_md(tmp_path: Path) -> None:
    src = _make_xlsx(tmp_path / "sheet.xlsx")
    out_dir = tmp_path / "sheet_docling"

    result = runner.invoke(app, [str(src), "-o", str(out_dir), "--stdout"])

    assert result.exit_code == 0, result.output
    document_md = (out_dir / "document.md").read_text(encoding="utf-8")
    assert document_md
    assert document_md in result.stdout


def test_stdout_prints_on_cached_run(tmp_path: Path) -> None:
    """A second invocation hits the document.md cache but must still print it."""
    src = _make_xlsx(tmp_path / "sheet.xlsx")
    out_dir = tmp_path / "sheet_docling"

    first = runner.invoke(app, [str(src), "-o", str(out_dir), "--stdout"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(app, [str(src), "-o", str(out_dir), "--stdout"])
    assert second.exit_code == 0, second.output
    document_md = (out_dir / "document.md").read_text(encoding="utf-8")
    assert document_md in second.stdout


def test_stdout_without_flag_does_not_print_document(tmp_path: Path) -> None:
    src = _make_xlsx(tmp_path / "sheet.xlsx")
    out_dir = tmp_path / "sheet_docling"

    result = runner.invoke(app, [str(src), "-o", str(out_dir)])

    assert result.exit_code == 0, result.output
    document_md = (out_dir / "document.md").read_text(encoding="utf-8")
    assert document_md not in result.stdout


def test_print_document_stdout_missing_file_does_not_raise(tmp_path: Path) -> None:
    _print_document_stdout(tmp_path)  # no document.md in this empty dir; must not raise
