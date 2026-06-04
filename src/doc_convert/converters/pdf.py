"""PDF converter: local Docling extraction with OCR, tables, and figure captions."""

from __future__ import annotations

import logging

from doc_convert.base import BaseConverter
from doc_convert.markdown import (
    build_images_catalog,
    build_page_annotated_markdown,
    get_document_title,
    get_pdf_metadata,
)

logger = logging.getLogger(__name__)


class PdfConverter(BaseConverter):
    """Local Docling pipeline: layout + OCR + tables + figure extraction.

    Figure captions are produced by ``BaseConverter.describe_figures`` so the
    same captioner setting (--captions) is honored on PDF, DOCX, and PPTX.
    """

    def convert(self) -> None:
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.datamodel.pipeline_options import PdfPipelineOptions  # noqa: PLC0415
        from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: PLC0415
        from docling_core.types.doc import ImageRefMode  # noqa: PLC0415

        from doc_convert.vlm import is_mps_float64_error  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()

        def build_converter(force_cpu: bool) -> DocumentConverter:
            pipeline_options = PdfPipelineOptions(
                do_ocr=self.options.do_ocr,
                do_table_structure=True,
                generate_page_images=self.options.figures,
                generate_picture_images=self.options.figures,
                do_picture_description=False,
                do_picture_classification=self.options.figures,
            )
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

        figure_map: dict[str, str] = {}
        figure_descriptions: dict[str, str] = {}
        fig_count = 0
        if self.options.figures:
            figure_map, image_paths, item_refs = self.extract_figures_from_doc(doc)
            fig_count = len(figure_map)
            figure_descriptions = self.describe_figures(image_paths, item_refs, "vlm.describe_pdf_images")

        title = get_document_title(doc, str(self.source))
        pdf_meta = get_pdf_metadata(str(self.source))

        page_md = build_page_annotated_markdown(
            doc, figure_map, title, pdf_meta, figure_descriptions=figure_descriptions
        )
        self.write_document_md(page_md)

        if fig_count > 0:
            catalog = build_images_catalog(doc, figure_map, figure_descriptions)
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
            captions_used=self.options.captions_enabled and fig_count > 0,
            desc_count=len(figure_descriptions),
        )
