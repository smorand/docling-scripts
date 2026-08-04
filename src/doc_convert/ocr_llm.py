"""LLM-backed OCR engine for the local Docling PDF pipeline.

Docling's OCR stage extracts text from *bitmap* regions of a page (scanned
content, embedded screenshots, stamped logos) and returns ``TextCell`` objects
with bounding boxes. The built-in engines (Tesseract, RapidOCR, EasyOCR) do
this locally. This module plugs a cloud LLM into the same contract: each OCR
rectangle is cropped, sent to the model, and the transcribed text becomes a
single ``TextCell`` covering that region.

This keeps the rest of the local pipeline intact (layout, tables, figures,
formulas) while letting ``--ocr-model provider/model`` (e.g.
``ibm/claude-sonnet-4-5``) handle the text-from-image step.

Note: docling crops *regions* before OCR, so a fully-scanned page becomes one
big region read as a single blob (no internal reading order). For fully-scanned
documents ``--engine llm`` (whole-page markdown) is usually a better fit; this
engine shines on mostly-digital documents with small embedded image-text.
"""

from __future__ import annotations

import contextlib
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from docling.datamodel.pipeline_options import OcrOptions
from docling.models.base_ocr_model import BaseOcrModel
from docling_core.types.doc.page import BoundingRectangle, TextCell

if TYPE_CHECKING:
    from collections.abc import Iterable

    from docling.datamodel.base_models import Page
    from docling.datamodel.document import ConversionResult

logger = logging.getLogger(__name__)

# Crop multiplier (72 dpi * 3 == 216 dpi); matches docling's built-in OCR models.
_OCR_IMAGE_SCALE = 3


class LlmOcrOptions(OcrOptions):
    """Pipeline options selecting the LLM OCR engine.

    ``lang`` is inherited from :class:`OcrOptions` but ignored: the LLM reads
    any language from the prompt. ``provider``/``model`` name the cloud model.
    """

    kind: ClassVar[str] = "llm_ocr"
    provider: str
    model: str
    lang: list[str] = ["auto"]  # noqa: RUF012 - pydantic copies field defaults per-instance


class LlmOcrModel(BaseOcrModel):
    """OCR model that transcribes each bitmap region with a cloud LLM."""

    def __init__(
        self,
        *,
        enabled: bool,
        artifacts_path: Path | None,
        options: LlmOcrOptions,
        accelerator_options: object,
    ) -> None:
        super().__init__(
            enabled=enabled,
            artifacts_path=artifacts_path,
            options=options,
            accelerator_options=accelerator_options,  # type: ignore[arg-type]
        )
        self.options: LlmOcrOptions = options
        self.scale = _OCR_IMAGE_SCALE

    @classmethod
    def get_options_type(cls) -> type[OcrOptions]:
        return LlmOcrOptions

    def __call__(
        self,
        conv_res: ConversionResult,  # noqa: ARG002 - required by the docling page-model contract
        page_batch: Iterable[Page],
    ) -> Iterable[Page]:
        if not self.enabled:
            yield from page_batch
            return

        from config import Settings  # noqa: PLC0415
        from doc_convert.converters.image import convert_image_to_markdown  # noqa: PLC0415
        from doc_convert.providers import get_ocr_prompt  # noqa: PLC0415

        settings = Settings()
        prompt = get_ocr_prompt()

        for page in page_batch:
            if page._backend is None or not page._backend.is_valid():
                yield page
                continue

            ocr_rects = self.get_ocr_rects(page)
            cells: list[TextCell] = []
            for idx, ocr_rect in enumerate(ocr_rects):
                if ocr_rect.area() == 0:
                    continue
                crop = page._backend.get_page_image(scale=self.scale, cropbox=ocr_rect)
                with tempfile.NamedTemporaryFile(suffix=".png", mode="w+b", delete=False) as fh:
                    tmp_path = Path(fh.name)
                    crop.save(fh, "PNG")
                try:
                    text = convert_image_to_markdown(
                        tmp_path, self.options.provider, self.options.model, settings, prompt=prompt
                    ).strip()
                except Exception as exc:
                    logger.warning("LLM OCR failed for region %d on a page: %s", idx, exc)
                    continue
                finally:
                    tmp_path.unlink(missing_ok=True)

                if not text:
                    continue
                cells.append(
                    TextCell(
                        index=idx,
                        text=text,
                        orig=text,
                        from_ocr=True,
                        confidence=1.0,
                        rect=BoundingRectangle.from_bounding_box(ocr_rect),
                    )
                )

            self.post_process_cells(cells, page, conv_res)
            yield page


def register_llm_ocr() -> None:
    """Register :class:`LlmOcrModel` with docling's OCR factory.

    ``get_ocr_factory`` is ``lru_cache``d, so the instance we register into is
    the same one the pipeline later resolves against (for the default
    ``allow_external_plugins=False``). Idempotent: re-registration is a no-op.
    """
    from docling.models.factories import get_ocr_factory  # noqa: PLC0415

    factory = get_ocr_factory(allow_external_plugins=False)
    # ValueError → the kind is already registered in this process (idempotent).
    with contextlib.suppress(ValueError):
        factory.register(LlmOcrModel, "doc_convert", "doc_convert.ocr_llm")
