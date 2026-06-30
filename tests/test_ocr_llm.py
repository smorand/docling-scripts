"""Tests for the LLM-backed OCR engine and its docling factory registration."""

from __future__ import annotations

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
