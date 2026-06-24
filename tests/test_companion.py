"""Tests for companion file detection and reference resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_convert.companion import (
    _load_text_file,
    _read_cached_companion,
    detect_companion,
    resolve_reference_paths,
)


def test_detect_companion_sibling(tmp_path: Path) -> None:
    source = tmp_path / "meeting.ogg"
    source.write_bytes(b"\x00")
    companion = tmp_path / "meeting.md"
    companion.write_text("notes")
    assert detect_companion(source) == companion


def test_detect_companion_absent(tmp_path: Path) -> None:
    source = tmp_path / "meeting.ogg"
    source.write_bytes(b"\x00")
    assert detect_companion(source) is None


def test_detect_companion_name_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Weekly Sync.md").write_text("agenda")
    found = detect_companion(tmp_path / "ignored.ogg", name_override="Weekly Sync")
    assert found == tmp_path / "Weekly Sync.md"


def test_load_text_file_ok(tmp_path: Path) -> None:
    f = tmp_path / "notes.md"
    f.write_text("hello")
    assert _load_text_file(f) == "hello"


def test_load_text_file_binary_returns_none(tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x00\x01")
    assert _load_text_file(f) is None


def test_resolve_reference_paths_markdown_link(tmp_path: Path) -> None:
    img = tmp_path / "diagram.png"
    img.write_bytes(b"\x89PNG")
    companion = tmp_path / "meeting.md"
    companion.write_text("See ![arch](diagram.png) and https://example.com/skip.png")
    paths = resolve_reference_paths(companion)
    assert img.resolve() in paths
    # URLs are not resolved to local files
    assert all("example.com" not in str(p) for p in paths)


def test_resolve_reference_paths_skips_missing(tmp_path: Path) -> None:
    companion = tmp_path / "meeting.md"
    companion.write_text("[gone](nope.png)")
    assert resolve_reference_paths(companion) == []


def test_read_cached_companion_with_lang_sentinel(tmp_path: Path) -> None:
    cache = tmp_path / "companion_context.md"
    cache.write_text("<!-- doc-convert:lang=fr -->\nBonjour")
    lang, body = _read_cached_companion(cache)
    assert lang == "fr"
    assert body == "Bonjour"


def test_read_cached_companion_without_sentinel(tmp_path: Path) -> None:
    cache = tmp_path / "companion_context.md"
    cache.write_text("plain body")
    lang, body = _read_cached_companion(cache)
    assert lang is None
    assert body == "plain body"
