"""Tests for output directory resolution, caching, and the doc symlink."""

from __future__ import annotations

from pathlib import Path

from doc_convert.output import (
    check_cache,
    check_step_cache,
    make_document_symlink,
    resolve_output_dir,
)


def test_resolve_output_dir_with_source() -> None:
    src = Path("/tmp/docs/report.pdf")
    out = resolve_output_dir(src, "report", None)
    assert out == Path("/tmp/docs/report_docling")


def test_resolve_output_dir_none_uses_cwd() -> None:
    out = resolve_output_dir(None, "clip", None)
    assert out == Path.cwd() / "clip_docling"


def test_resolve_output_dir_override() -> None:
    out = resolve_output_dir(Path("/tmp/x.pdf"), "x", "/custom/dir")
    assert out == Path("/custom/dir")


def test_check_step_cache(tmp_path: Path) -> None:
    assert check_step_cache(tmp_path, "document.md", force=False) is False
    (tmp_path / "document.md").write_text("hi")
    assert check_step_cache(tmp_path, "document.md", force=False) is True
    # force always re-runs
    assert check_step_cache(tmp_path, "document.md", force=True) is False


def test_check_cache_empty_dir(tmp_path: Path) -> None:
    out = tmp_path / "out_docling"
    out.mkdir()
    # empty dir is not a cache hit
    assert check_cache(out, force=False) is False
    (out / "document.md").write_text("x")
    assert check_cache(out, force=False) is True
    assert check_cache(out, force=True) is False


def test_make_document_symlink_creates_link(tmp_path: Path) -> None:
    out = tmp_path / "note_docling"
    out.mkdir()
    (out / "document.md").write_text("content")
    make_document_symlink(out)
    link = tmp_path / "note.md"
    assert link.is_symlink()
    assert link.resolve() == (out / "document.md").resolve()


def test_make_document_symlink_skips_without_document(tmp_path: Path) -> None:
    out = tmp_path / "note_docling"
    out.mkdir()
    make_document_symlink(out)
    assert not (tmp_path / "note.md").exists()


def test_make_document_symlink_keeps_existing_regular_file(tmp_path: Path) -> None:
    out = tmp_path / "meeting_docling"
    out.mkdir()
    (out / "document.md").write_text("content")
    # a companion .md already sits next to the output dir
    companion = tmp_path / "meeting.md"
    companion.write_text("my notes")
    make_document_symlink(out)
    assert not companion.is_symlink()
    assert companion.read_text() == "my notes"
