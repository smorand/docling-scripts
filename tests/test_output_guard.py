"""Tests for the output directory guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_convert import output_guard


@pytest.fixture(autouse=True)
def _reset_guards() -> None:
    output_guard._guards.clear()
    yield
    output_guard._guards.clear()


def test_is_clean_exit() -> None:
    assert output_guard.is_clean_exit(None) is True
    assert output_guard.is_clean_exit(SystemExit(0)) is True
    assert output_guard.is_clean_exit(SystemExit(None)) is True
    assert output_guard.is_clean_exit(SystemExit(1)) is False
    assert output_guard.is_clean_exit(RuntimeError("boom")) is False


def test_cleanup_removes_newly_created_empty_dir(tmp_path: Path) -> None:
    out = tmp_path / "doc_docling"
    output_guard.register(out)
    out.mkdir()
    (out / "scratch.tmp").write_text("partial")
    output_guard.cleanup_pending()
    assert not out.exists()


def test_cleanup_keeps_dir_with_marker(tmp_path: Path) -> None:
    out = tmp_path / "doc_docling"
    output_guard.register(out)
    out.mkdir()
    (out / "document.md").write_text("done")
    output_guard.cleanup_pending()
    assert out.exists()
    assert (out / "document.md").exists()


def test_cleanup_preserves_preexisting_content(tmp_path: Path) -> None:
    out = tmp_path / "doc_docling"
    out.mkdir()
    (out / "kept.txt").write_text("old")
    output_guard.register(out)  # registered after it already exists
    (out / "added.tmp").write_text("new from this run")
    output_guard.cleanup_pending()
    assert out.exists()
    assert (out / "kept.txt").exists()
    assert not (out / "added.tmp").exists()


def test_cleanup_no_dir_is_noop(tmp_path: Path) -> None:
    out = tmp_path / "never_created_docling"
    output_guard.register(out)
    output_guard.cleanup_pending()  # must not raise
    assert not out.exists()


def test_has_keep_marker(tmp_path: Path) -> None:
    assert output_guard._has_keep_marker(tmp_path) is False
    (tmp_path / "audio.ogg").write_bytes(b"\x00")
    assert output_guard._has_keep_marker(tmp_path) is True
