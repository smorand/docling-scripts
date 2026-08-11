"""Tests for VLM helper utilities."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PIL import Image

from config import Settings
from doc_convert.vlm import describe_images_with_external_llm, is_mps_float64_error


def test_is_mps_float64_error_positive() -> None:
    assert is_mps_float64_error(RuntimeError("MPS does not support float64"))
    assert is_mps_float64_error(TypeError("Cannot convert: mps doesn't support this"))


def test_is_mps_float64_error_negative() -> None:
    assert not is_mps_float64_error(RuntimeError("CUDA out of memory"))
    assert not is_mps_float64_error(ValueError("float64 overflow on cpu"))


# ---------------------------------------------------------------------------
# describe_images_with_external_llm: concurrent captioning, no docling detour
# ---------------------------------------------------------------------------


def _png(path: Path, size: tuple[int, int] = (120, 90)) -> Path:
    Image.new("RGB", size, (200, 40, 40)).save(path, format="PNG")
    return path


def test_captions_keep_input_order_under_concurrency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings
) -> None:
    """A caption must land on its own figure whatever the completion order."""
    paths = [_png(tmp_path / f"f{i}.png") for i in range(12)]

    def fake_describe(image_path: Path, *_args: object, **_kwargs: object) -> str:
        time.sleep((12 - int(image_path.stem[1:])) * 0.002)  # later figures finish first
        return f"caption of {image_path.stem}"

    monkeypatch.setattr("doc_convert.vision_llm.describe_image", fake_describe)
    out = describe_images_with_external_llm(paths, "ibm", "m", ibm_settings, concurrency=4)

    assert out == [f"caption of f{i}" for i in range(12)]


def test_captions_pass_the_per_image_context_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings
) -> None:
    """The context block is prepended per image; mixing them up would caption the
    wrong figure with the right words."""
    paths = [_png(tmp_path / "a.png"), _png(tmp_path / "b.png")]
    seen: dict[str, str] = {}

    def fake_describe(image_path: Path, prompt: str, *_args: object, **_kwargs: object) -> str:
        seen[image_path.stem] = prompt
        return "ok"

    monkeypatch.setattr("doc_convert.vision_llm.describe_image", fake_describe)
    describe_images_with_external_llm(
        paths, "ibm", "m", ibm_settings, prompt="BASE", contexts=["CTX-A ", ""], concurrency=2
    )

    assert seen["a"] == "CTX-A BASE"
    assert seen["b"] == "BASE"


def test_captions_tolerate_one_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings) -> None:
    """A figure that exhausts its retries yields "", the others still get captions."""
    paths = [_png(tmp_path / f"f{i}.png") for i in range(4)]

    def fake_describe(image_path: Path, *_args: object, **_kwargs: object) -> str:
        return "" if image_path.stem == "f2" else "ok"

    monkeypatch.setattr("doc_convert.vision_llm.describe_image", fake_describe)
    out = describe_images_with_external_llm(paths, "ibm", "m", ibm_settings, concurrency=4)

    assert out == ["ok", "ok", "", "ok"]


def test_captions_empty_input_makes_no_call(monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings) -> None:
    def boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("no image, no call")

    monkeypatch.setattr("doc_convert.vision_llm.describe_image", boom)
    assert describe_images_with_external_llm([], "ibm", "m", ibm_settings) == []


def test_captions_reject_mismatched_contexts(tmp_path: Path, ibm_settings: Settings) -> None:
    with pytest.raises(ValueError, match="contexts length"):
        describe_images_with_external_llm([_png(tmp_path / "a.png")], "ibm", "m", ibm_settings, contexts=["x", "y"])


def test_captions_do_not_go_through_docling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings
) -> None:
    """Regression guard: routing single figures through docling's VlmPipeline cost
    ~145 s of pipeline construction on a 99-figure deck, upscaled every image 2x
    for no measured benefit, and forced sequential execution."""

    def boom(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("captions must not call convert_image_to_markdown")

    monkeypatch.setattr("doc_convert.converters.image.convert_image_to_markdown", boom)
    monkeypatch.setattr("doc_convert.vision_llm.describe_image", lambda *a, **k: "ok")
    assert describe_images_with_external_llm([_png(tmp_path / "a.png")], "ibm", "m", ibm_settings) == ["ok"]
