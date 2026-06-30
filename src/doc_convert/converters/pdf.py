"""PDF converter: local Docling extraction with OCR, tables, and figure captions."""

from __future__ import annotations

import logging

from doc_convert.base import BaseConverter
from doc_convert.markdown import (
    FloatingArtifacts,
    FloatingContext,
    build_document_markdown,
    build_images_catalog,
    collect_floating_contexts,
    get_document_title,
    get_pdf_metadata,
)

logger = logging.getLogger(__name__)


class PdfConverter(BaseConverter):
    """Local Docling pipeline: layout + OCR + tables + figure/table captioning.

    Figure descriptions are produced by ``BaseConverter.describe_figures`` and
    table descriptions by ``BaseConverter.describe_tables``; both honour the
    ``--captions`` setting and reuse the same captioner.
    """

    def convert(self) -> None:  # noqa: PLR0915
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.datamodel.pipeline_options import (  # noqa: PLC0415
            PdfPipelineOptions,
            TableStructureV2Options,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: PLC0415
        from docling_core.types.doc import ImageRefMode  # type: ignore[attr-defined]  # noqa: PLC0415

        from doc_convert.vlm import is_mps_float64_error  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()
        do_ocr = self.options.ocr_enabled

        def build_converter(force_cpu: bool) -> DocumentConverter:
            pipeline_options = PdfPipelineOptions(
                do_ocr=do_ocr,
                do_table_structure=True,
                table_structure_options=TableStructureV2Options(),
                do_formula_enrichment=True,
                # do_chart_extraction left off by default: our cloud captioner
                # (see _DEFAULT_CAPTION_PROMPT, items 4 and 5) already extracts
                # chart structure as a markdown table during figure description,
                # so Granite Vision (~5 GB) would be a redundant download.
                do_chart_extraction=False,
                generate_page_images=self.options.figures,
                generate_picture_images=self.options.figures,
                do_picture_description=False,
                do_picture_classification=self.options.figures,
                images_scale=2.0,
            )
            if do_ocr:
                pipeline_options.ocr_options = _build_ocr_options(self.options.ocr)
            if force_cpu or self.options.cpu:
                from docling.datamodel.accelerator_options import (  # noqa: PLC0415
                    AcceleratorDevice,
                    AcceleratorOptions,
                )

                pipeline_options.accelerator_options = AcceleratorOptions(device=AcceleratorDevice.CPU)
                if force_cpu and not self.options.cpu:
                    logger.warning("Falling back to CPU accelerator after MPS float64 error")
                else:
                    logger.info("Forcing CPU accelerator for Docling pipeline")
            return DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )

        with trace_span("docling.convert_pdf_local", file=self.source.name):
            logger.info("Converting %s (local pipeline)", self.source.name)
            try:
                result = build_converter(force_cpu=False).convert(str(self.source))
            except RuntimeError as exc:
                if not is_mps_float64_error(exc):
                    raise
                result = build_converter(force_cpu=True).convert(str(self.source))
            logger.info("Status: %s", result.status)

        doc = result.document
        contexts = collect_floating_contexts(doc)
        context_blocks = _build_context_blocks(contexts)
        artifacts = FloatingArtifacts()

        fig_count = 0
        if self.options.figures:
            figure_map, image_paths, item_refs = self.extract_figures_from_doc(doc)
            artifacts.figure_paths = figure_map
            fig_count = len(figure_map)
            artifacts.figure_descriptions = self.describe_figures(
                image_paths, item_refs, "vlm.describe_pdf_images", context_blocks
            )

        tbl_count = 0
        if self.options.figures and self.options.captions_enabled:
            tbl_paths, tbl_refs = self.extract_table_images(doc)
            tbl_count = len(tbl_paths)
            artifacts.table_descriptions = self.describe_tables(
                tbl_paths, tbl_refs, "vlm.describe_pdf_tables", context_blocks
            )

        title = get_document_title(doc, str(self.source))
        pdf_meta = get_pdf_metadata(str(self.source))

        page_md = build_document_markdown(doc, artifacts, contexts, title=title, pdf_meta=pdf_meta, paginated=True)
        self.write_document_md(page_md)

        if fig_count > 0:
            catalog = build_images_catalog(doc, artifacts, contexts)
            (self.output_dir / "images.md").write_text(catalog)

        if self.options.all_formats:
            self.write_all_formats(doc)
            (self.output_dir / "output_embedded.md").write_text(
                doc.export_to_markdown(image_mode=ImageRefMode.EMBEDDED)
            )

        if title:
            logger.info("Title: %s", title)
        self.print_summary(
            fig_count=fig_count,
            captions_used=self.options.captions_enabled and (fig_count > 0 or tbl_count > 0),
            desc_count=len(artifacts.figure_descriptions) + len(artifacts.table_descriptions),
        )


def _build_ocr_options(spec: object):  # type: ignore[no-untyped-def]
    """Map an :class:`OcrSpec` to docling OCR options.

    ``OcrLocal`` → Tesseract CLI (cross-platform, uses the system binary).
    ``OcrLlm`` → the custom :class:`LlmOcrModel`, registered on first use.
    """
    from doc_convert.formats import OcrLlm  # noqa: PLC0415

    if isinstance(spec, OcrLlm):
        from doc_convert.ocr_llm import LlmOcrOptions, register_llm_ocr  # noqa: PLC0415

        register_llm_ocr()
        logger.info("OCR engine: LLM (%s/%s)", spec.provider, spec.model)
        return LlmOcrOptions(provider=spec.provider, model=spec.model)

    from docling.datamodel.pipeline_options import TesseractCliOcrOptions  # noqa: PLC0415

    logger.info("OCR engine: Tesseract CLI")
    return TesseractCliOcrOptions()


def _build_context_blocks(contexts: dict[str, FloatingContext]) -> dict[str, str]:
    """Convert FloatingContext map → {ref: prompt-ready context block}."""
    from doc_convert.providers import build_context_block  # noqa: PLC0415

    return {ref: build_context_block(caption=ctx.caption, mention=ctx.mention) for ref, ctx in contexts.items()}
