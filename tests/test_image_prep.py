"""Tests for image_prep.ensure_image_under_limit."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from doc_convert import image_prep as ip
from doc_convert.image_prep import ensure_image_under_limit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(path: Path, width: int, height: int, color: tuple[int, int, int] = (200, 100, 50)) -> Path:
    """Write a solid-color PNG to *path* and return the path."""
    img = Image.new("RGB", (width, height), color)
    img.save(path, format="PNG")
    return path


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Tests: small image is returned as-is
# ---------------------------------------------------------------------------


def test_small_image_is_unchanged(tmp_path: Path) -> None:
    """A 100x100 PNG (well under 5 MB) must be returned unchanged."""
    src = _make_png(tmp_path / "small.png", 100, 100)
    prepared = ensure_image_under_limit(src)
    assert prepared.path == src
    assert not prepared._is_tmp


def test_small_image_context_manager(tmp_path: Path) -> None:
    """Context-manager exit on a non-tmp image must not delete the original."""
    src = _make_png(tmp_path / "small.png", 100, 100)
    with ensure_image_under_limit(src) as p:
        assert p.path == src
    assert src.exists()


# ---------------------------------------------------------------------------
# Tests: oversized image gets recompressed
# ---------------------------------------------------------------------------


def test_large_png_gets_compressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A PNG whose file size exceeds MAX_IMAGE_BYTES must be recompressed.

    We monkeypatch MAX_IMAGE_BYTES to a tiny threshold so we can exercise
    the compression path with a small test image.
    """
    src = _make_png(tmp_path / "big.png", 200, 200)
    original_size = src.stat().st_size

    # Pretend the limit is 1 byte so the image always exceeds it.
    monkeypatch.setattr(ip, "MAX_IMAGE_BYTES", 1)

    prepared = ensure_image_under_limit(src, max_bytes=1)
    try:
        assert prepared._is_tmp
        assert prepared.path != src
        assert prepared.path.suffix.lower() == ".jpg"
        assert prepared.path.exists()
    finally:
        prepared.cleanup()

    # Original is untouched.
    assert src.exists()
    assert src.stat().st_size == original_size


def test_large_png_context_manager_cleanup(tmp_path: Path) -> None:
    """Tmp file created during compression is deleted on context-manager exit."""
    src = _make_png(tmp_path / "big.png", 300, 300)

    with ensure_image_under_limit(src, max_bytes=1) as p:
        tmp = p.path
        assert tmp.exists()

    assert not tmp.exists()


# ---------------------------------------------------------------------------
# Tests: PreparedImage cleanup is idempotent
# ---------------------------------------------------------------------------


def test_double_cleanup_is_safe(tmp_path: Path) -> None:
    """Calling cleanup() twice must not raise."""
    src = _make_png(tmp_path / "img.png", 50, 50)
    prepared = ensure_image_under_limit(src, max_bytes=1)
    prepared.cleanup()
    prepared.cleanup()  # must not raise


# ---------------------------------------------------------------------------
# Tests: JPEG quality loop
# ---------------------------------------------------------------------------


def test_output_is_valid_jpeg(tmp_path: Path) -> None:
    """The compressed output must be a valid JPEG that PIL can open."""
    src = _make_png(tmp_path / "img.png", 400, 400)

    with ensure_image_under_limit(src, max_bytes=1) as p:
        reopened = Image.open(p.path)
        assert reopened.format == "JPEG"


def test_rgba_png_is_handled(tmp_path: Path) -> None:
    """RGBA PNGs (transparency) must be converted to RGB before JPEG encoding."""
    img = Image.new("RGBA", (200, 200), (255, 0, 0, 128))
    src = tmp_path / "rgba.png"
    img.save(src, format="PNG")

    with ensure_image_under_limit(src, max_bytes=1) as p:
        reopened = Image.open(p.path)
        assert reopened.mode == "RGB"
