"""Tests for the LLM-backed OCR engine and its docling factory registration."""

from __future__ import annotations

import threading

import pytest
from docling_core.types.doc.base import BoundingBox
from PIL import Image

from config import Settings
from doc_convert.formats import OcrLlm, OcrLocal, OcrOff
from doc_convert.ocr_llm import LlmOcrModel, LlmOcrOptions, register_llm_ocr


def test_llm_ocr_options_kind_and_fields() -> None:
    opt = LlmOcrOptions(provider="ibm", model="claude-haiku-4-5")
    assert opt.kind == "llm_ocr"
    assert opt.provider == "ibm"
    assert opt.model == "claude-haiku-4-5"
    # lang is inherited but ignored by the LLM; it must have a usable default.
    assert opt.lang == ["auto"]


def test_get_options_type() -> None:
    assert LlmOcrModel.get_options_type() is LlmOcrOptions


def test_register_llm_ocr_is_idempotent() -> None:
    from docling.models.factories import get_ocr_factory  # noqa: PLC0415

    register_llm_ocr()
    register_llm_ocr()  # second call must not raise
    factory = get_ocr_factory(allow_external_plugins=False)
    assert "llm_ocr" in factory.registered_kind


def test_factory_resolves_custom_model() -> None:
    from docling.datamodel.accelerator_options import AcceleratorOptions  # noqa: PLC0415
    from docling.models.factories import get_ocr_factory  # noqa: PLC0415

    register_llm_ocr()
    factory = get_ocr_factory(allow_external_plugins=False)
    inst = factory.create_instance(
        options=LlmOcrOptions(provider="ibm", model="claude-haiku-4-5"),
        enabled=True,
        artifacts_path=None,
        accelerator_options=AcceleratorOptions(),
    )
    assert isinstance(inst, LlmOcrModel)


def test_build_ocr_options_dispatch() -> None:
    from docling.datamodel.pipeline_options import TesseractCliOcrOptions  # noqa: PLC0415

    from doc_convert.converters.pdf import _build_ocr_options  # noqa: PLC0415

    assert isinstance(_build_ocr_options(OcrLocal("tesseract")), TesseractCliOcrOptions)
    llm_opts = _build_ocr_options(OcrLlm("ibm", "claude-haiku-4-5"))
    assert isinstance(llm_opts, LlmOcrOptions)
    assert (llm_opts.provider, llm_opts.model) == ("ibm", "claude-haiku-4-5")


def test_ocr_off_disables() -> None:
    from pathlib import Path  # noqa: PLC0415

    from doc_convert.base import ConvertOptions  # noqa: PLC0415

    assert ConvertOptions(output_dir=Path("/tmp/x"), ocr=OcrOff()).ocr_enabled is False
    assert ConvertOptions(output_dir=Path("/tmp/x"), ocr=OcrLocal("tesseract")).ocr_enabled is True


def test_post_process_cells_call_matches_installed_docling() -> None:
    """Regression: LlmOcrModel must call post_process_cells with the arity the
    installed docling actually declares.

    A previous change passed an extra ``conv_res`` argument, which raised
    ``TypeError`` the moment OCR actually fired on a bitmap region. Since the
    LLM OCR engine is the default --ocr-model, that crashed scanned-PDF
    conversions. Pin the contract instead of trusting it.
    """
    import ast  # noqa: PLC0415
    import inspect  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from docling.models.base_ocr_model import BaseOcrModel  # noqa: PLC0415

    expected = [p for p in inspect.signature(BaseOcrModel.post_process_cells).parameters if p != "self"]

    source = Path(inspect.getfile(LlmOcrModel)).read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "post_process_cells"
    ]

    assert calls, "LlmOcrModel is expected to call post_process_cells"
    for call in calls:
        assert len(call.args) == len(expected), (
            f"post_process_cells called with {len(call.args)} positional args, "
            f"but installed docling declares {len(expected)}: {expected}"
        )


# ---------------------------------------------------------------------------
# Concurrent region transcription
# ---------------------------------------------------------------------------


def _rect(area: float = 100.0) -> BoundingBox:
    """A real docling BoundingBox, since post_process_cells consumes one."""
    side = area**0.5
    return BoundingBox(l=0, t=0, r=side, b=side)


class _FakeBackend:
    def __init__(self, valid: bool = True) -> None:
        self.valid = valid
        self.crop_threads: list[str] = []

    def is_valid(self) -> bool:
        return self.valid

    def get_page_image(self, scale: float, cropbox: object) -> object:
        self.crop_threads.append(threading.current_thread().name)
        return Image.new("RGB", (30, 20), (255, 255, 255))


class _FakePage:
    def __init__(self, backend: _FakeBackend | None) -> None:
        self._backend = backend


def _model(monkeypatch: pytest.MonkeyPatch, rects_per_page: list[list[BoundingBox]]) -> tuple[object, list]:
    """Build an enabled LlmOcrModel with faked rect detection and cell post-processing."""
    model = LlmOcrModel.__new__(LlmOcrModel)
    model.enabled = True
    model.options = LlmOcrOptions(provider="ibm", model="m")
    model.scale = 3
    queue = list(rects_per_page)
    monkeypatch.setattr(type(model), "get_ocr_rects", lambda _self, _page: queue.pop(0), raising=False)
    processed: list[tuple[list, object]] = []
    monkeypatch.setattr(
        type(model), "post_process_cells", lambda _self, cells, page: processed.append((cells, page)), raising=False
    )
    return model, processed


def test_regions_are_cropped_on_the_calling_thread(monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings) -> None:
    """docling's PDF backends are not thread-safe, so get_page_image must never be
    touched from a worker. Only the API calls are allowed to fan out."""
    monkeypatch.setattr("config.Settings", lambda: ibm_settings)
    monkeypatch.setattr("doc_convert.vision_llm.describe_encoded", lambda *a, **k: "text")

    backends = [_FakeBackend(), _FakeBackend()]
    pages = [_FakePage(b) for b in backends]
    model, _ = _model(monkeypatch, [[_rect(), _rect()], [_rect()]])

    list(model(None, pages))

    main = threading.current_thread().name
    for backend in backends:
        assert backend.crop_threads, "each page must be cropped"
        assert all(name == main for name in backend.crop_threads), (
            f"cropping leaked onto worker threads: {backend.crop_threads}"
        )


def test_each_region_lands_on_its_own_page(monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings) -> None:
    """Completion order is nondeterministic; a region's text must not migrate to
    another page."""
    monkeypatch.setattr("config.Settings", lambda: ibm_settings)
    seen: dict[str, str] = {}

    def fake(_mime: str, b64: str, *_a: object, **kwargs: object) -> str:
        label = str(kwargs.get("label", ""))
        seen[label] = b64
        return f"text for {label}"

    monkeypatch.setattr("doc_convert.vision_llm.describe_encoded", fake)
    pages = [_FakePage(_FakeBackend()), _FakePage(_FakeBackend())]
    model, processed = _model(monkeypatch, [[_rect(), _rect()], [_rect()]])

    list(model(None, pages))

    assert len(processed) == 2, "post_process_cells runs once per page"
    assert [len(cells) for cells, _ in processed] == [2, 1]
    assert [c.index for c in processed[0][0]] == [0, 1]
    assert [c.index for c in processed[1][0]] == [0]


def test_one_failed_region_does_not_lose_the_others(monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings) -> None:
    """The previous implementation dropped a region on any exception with no retry,
    and a single ReadTimeout silently lost a whole page of text on a 20-page scan."""
    monkeypatch.setattr("config.Settings", lambda: ibm_settings)
    calls = {"n": 0}

    def fake(*_a: object, **_k: object) -> str:
        calls["n"] += 1
        return "" if calls["n"] == 2 else "recovered text"

    monkeypatch.setattr("doc_convert.vision_llm.describe_encoded", fake)
    pages = [_FakePage(_FakeBackend())]
    model, processed = _model(monkeypatch, [[_rect(), _rect(), _rect()]])

    list(model(None, pages))

    cells, _ = processed[0]
    assert len(cells) == 2, "the two successful regions survive"
    assert [c.index for c in cells] == [0, 2]


def test_zero_area_regions_are_skipped(monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings) -> None:
    monkeypatch.setattr("config.Settings", lambda: ibm_settings)
    monkeypatch.setattr("doc_convert.vision_llm.describe_encoded", lambda *a, **k: "text")
    pages = [_FakePage(_FakeBackend())]
    model, processed = _model(monkeypatch, [[_rect(area=0.0), _rect()]])

    list(model(None, pages))

    assert len(processed[0][0]) == 1


def test_invalid_backend_page_is_passed_through(monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings) -> None:
    monkeypatch.setattr("config.Settings", lambda: ibm_settings)

    def boom(*_a: object, **_k: object) -> str:
        raise AssertionError("a page with no usable backend must not reach the API")

    monkeypatch.setattr("doc_convert.vision_llm.describe_encoded", boom)
    pages = [_FakePage(None), _FakePage(_FakeBackend(valid=False))]
    model, processed = _model(monkeypatch, [])

    out = list(model(None, pages))

    assert out == pages, "pages still flow through the pipeline"
    assert [len(cells) for cells, _ in processed] == [0, 0]


def test_disabled_model_is_a_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    model = LlmOcrModel.__new__(LlmOcrModel)
    model.enabled = False
    pages = [_FakePage(_FakeBackend())]
    assert list(model(None, pages)) == pages
