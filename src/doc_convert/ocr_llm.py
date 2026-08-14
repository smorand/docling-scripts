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
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from docling.datamodel.pipeline_options import OcrOptions
from docling.models.base_ocr_model import BaseOcrModel
from docling_core.types.doc.page import BoundingRectangle, TextCell

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from docling.datamodel.base_models import Page
    from docling.datamodel.document import ConversionResult
    from docling_core.types.doc.base import BoundingBox

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


@dataclass(frozen=True)
class _OcrJob:
    """One bitmap region waiting for transcription, already encoded."""

    page_index: int
    index: int
    rect: BoundingBox
    mime: str
    b64: str


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
        conv_res: ConversionResult,
        page_batch: Iterable[Page],
    ) -> Iterable[Page]:
        """Transcribe every bitmap region of the batch, several requests at a time.

        Cropping runs sequentially in this thread on purpose: docling's PDF
        backends are not thread-safe, and ``get_page_image`` is cheap local work.
        Only the API calls fan out, which is where the seconds are. Crops travel
        as encoded bytes rather than temporary files, so a page with fifty small
        regions no longer churns fifty files through the filesystem.
        """
        if not self.enabled:
            yield from page_batch
            return

        from config import Settings  # noqa: PLC0415
        from doc_convert.providers import DEFAULT_LLM_CONCURRENCY, get_ocr_prompt, require_api_key  # noqa: PLC0415
        from doc_convert.vision_llm import describe_encoded, encode_pil, make_client, map_concurrent  # noqa: PLC0415

        settings = Settings()
        prompt = get_ocr_prompt()
        api_key = require_api_key(self.options.provider, settings)

        pages = list(page_batch)
        jobs: list[_OcrJob] = []
        for page_index, page in enumerate(pages):
            if page._backend is None or not page._backend.is_valid():
                continue
            for idx, ocr_rect in enumerate(self.get_ocr_rects(page)):
                if ocr_rect.area() == 0:
                    continue
                crop = page._backend.get_page_image(scale=self.scale, cropbox=ocr_rect)
                mime, b64 = encode_pil(crop, label=f"ocr region {idx}")
                jobs.append(_OcrJob(page_index=page_index, index=idx, rect=ocr_rect, mime=mime, b64=b64))

        texts: list[str] = []
        if jobs:
            workers = max(1, min(DEFAULT_LLM_CONCURRENCY, len(jobs)))
            with make_client(settings, workers) as client:

                def work(job: _OcrJob) -> str:
                    return describe_encoded(
                        job.mime,
                        job.b64,
                        prompt,
                        self.options.provider,
                        self.options.model,
                        settings,
                        api_key,
                        client,
                        label=f"ocr region {job.index}",
                    ).strip()

                texts = map_concurrent(jobs, work, workers, what="OCR region")

        cells_by_page: dict[int, list[TextCell]] = {i: [] for i in range(len(pages))}
        for job, text in zip(jobs, texts, strict=True):
            if not text:
                logger.warning("LLM OCR returned nothing for region %d on page %d", job.index, job.page_index + 1)
                continue
            cells_by_page[job.page_index].append(
                TextCell(
                    index=job.index,
                    text=text,
                    orig=text,
                    from_ocr=True,
                    confidence=1.0,
                    rect=BoundingRectangle.from_bounding_box(job.rect),
                )
            )

        for page_index, page in enumerate(pages):
            self.post_process_cells(cells_by_page[page_index], page, conv_res, None)
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
