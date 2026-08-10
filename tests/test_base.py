"""Tests for BaseConverter.filter_figures_by_size (caption filter, Stage A)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from doc_convert.base import MIN_FIGURE_SIZE_PX, ConvertOptions
from doc_convert.converters.image import ImageConverter


def _make_png(path: Path, width: int, height: int) -> Path:
    Image.new("RGB", (width, height), (200, 100, 50)).save(path, format="PNG")
    return path


def _converter(tmp_path: Path, *, caption_filter: bool = True) -> ImageConverter:
    options = ConvertOptions(output_dir=tmp_path / "out", caption_filter=caption_filter)
    return ImageConverter(tmp_path / "unused.png", options)


def test_drops_figure_under_size_floor(tmp_path: Path) -> None:
    """A figure natively smaller than 64px on either axis is dropped."""
    small = _make_png(tmp_path / "icon.png", 32, 32)
    converter = _converter(tmp_path)

    figure_map, image_paths, item_refs = converter.filter_figures_by_size(
        {"#/pictures/0": "figures/icon.png"}, [small], ["#/pictures/0"]
    )

    assert figure_map == {}
    assert image_paths == []
    assert item_refs == []


def test_keeps_figure_above_size_floor(tmp_path: Path) -> None:
    """A figure at or above the floor on both axes is kept unchanged."""
    large = _make_png(tmp_path / "chart.png", 800, 600)
    converter = _converter(tmp_path)

    figure_map, image_paths, item_refs = converter.filter_figures_by_size(
        {"#/pictures/0": "figures/chart.png"}, [large], ["#/pictures/0"]
    )

    assert figure_map == {"#/pictures/0": "figures/chart.png"}
    assert image_paths == [large]
    assert item_refs == ["#/pictures/0"]


def test_boundary_exactly_at_floor_is_kept(tmp_path: Path) -> None:
    """Exactly MIN_FIGURE_SIZE_PX on both axes must be kept (>=, not >)."""
    boundary = _make_png(tmp_path / "boundary.png", MIN_FIGURE_SIZE_PX, MIN_FIGURE_SIZE_PX)
    converter = _converter(tmp_path)

    _, image_paths, _ = converter.filter_figures_by_size(
        {"#/pictures/0": "figures/boundary.png"}, [boundary], ["#/pictures/0"]
    )

    assert image_paths == [boundary]


def test_disabled_via_caption_filter_option_keeps_everything(tmp_path: Path) -> None:
    """--no-caption-filter (caption_filter=False) must restore the old behavior."""
    small = _make_png(tmp_path / "icon.png", 16, 16)
    converter = _converter(tmp_path, caption_filter=False)

    figure_map, image_paths, item_refs = converter.filter_figures_by_size(
        {"#/pictures/0": "figures/icon.png"}, [small], ["#/pictures/0"]
    )

    assert figure_map == {"#/pictures/0": "figures/icon.png"}
    assert image_paths == [small]
    assert item_refs == ["#/pictures/0"]


def test_unreadable_image_is_kept_fail_open(tmp_path: Path) -> None:
    """A file that fails to open as an image is kept, never dropped on error."""
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a real png")
    converter = _converter(tmp_path)

    figure_map, image_paths, item_refs = converter.filter_figures_by_size(
        {"#/pictures/0": "figures/broken.png"}, [broken], ["#/pictures/0"]
    )

    assert image_paths == [broken]
    assert item_refs == ["#/pictures/0"]
    assert figure_map == {"#/pictures/0": "figures/broken.png"}


def test_mixed_batch_keeps_only_large_figures(tmp_path: Path) -> None:
    """Several figures at once: only those clearing the floor survive, order preserved."""
    small = _make_png(tmp_path / "logo.png", 40, 40)
    large1 = _make_png(tmp_path / "photo.png", 500, 400)
    large2 = _make_png(tmp_path / "diagram.png", 300, 300)
    converter = _converter(tmp_path)

    figure_map, image_paths, item_refs = converter.filter_figures_by_size(
        {"#0": "figures/logo.png", "#1": "figures/photo.png", "#2": "figures/diagram.png"},
        [small, large1, large2],
        ["#0", "#1", "#2"],
    )

    assert item_refs == ["#1", "#2"]
    assert image_paths == [large1, large2]
    assert figure_map == {"#1": "figures/photo.png", "#2": "figures/diagram.png"}


def test_same_path_referenced_twice_uses_cached_size_lookup(tmp_path: Path) -> None:
    """Same on-disk file referenced by two refs (post exact-dedup): one size read, both kept/dropped together."""
    small = _make_png(tmp_path / "dup.png", 20, 20)
    converter = _converter(tmp_path)

    figure_map, image_paths, item_refs = converter.filter_figures_by_size(
        {"#0": "figures/dup.png", "#1": "figures/dup.png"},
        [small, small],
        ["#0", "#1"],
    )

    assert image_paths == []
    assert item_refs == []
    assert figure_map == {}
