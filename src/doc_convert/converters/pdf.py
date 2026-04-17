"""PDF converter: full local extraction with OCR, VLM, and figures."""

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
    """Full PDF extraction with local Docling pipeline."""

    def convert(self) -> None:
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.datamodel.pipeline_options import (  # noqa: PLC0415
            PdfPipelineOptions,
            PictureDescriptionVlmEngineOptions,
        )
        from docling.document_converter import DocumentConverter, PdfFormatOption  # noqa: PLC0415
        from docling_core.types.doc import ImageRefMode  # noqa: PLC0415

        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()

        pipeline_options = PdfPipelineOptions(
            do_ocr=self.options.do_ocr,
            do_table_structure=True,
            generate_page_images=self.options.figures,
            generate_picture_images=self.options.figures,
            do_picture_description=self.options.vlm and self.options.figures,
            do_picture_classification=self.options.figures,
            artifacts_path=str(self.options.models_path),
        )

        if self.options.vlm:
            pipeline_options.picture_description_options = PictureDescriptionVlmEngineOptions.from_preset(
                self.options.vlm_preset
            )
            logger.info("VLM picture description enabled (preset: %s)", self.options.vlm_preset)

        converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )

        with trace_span("docling.convert_pdf_local", file=self.source.name):
            logger.info("Converting %s (local pipeline)", self.source.name)
            result = converter.convert(str(self.source))
            logger.info("Status: %s", result.status)

        doc = result.document

        figure_map: dict[str, str] = {}
        fig_count = 0
        if self.options.figures:
            figure_map, _, _ = self.extract_figures_from_doc(doc)
            fig_count = len(figure_map)

        title = get_document_title(doc, str(self.source))
        pdf_meta = get_pdf_metadata(str(self.source))

        page_md = build_page_annotated_markdown(doc, figure_map, title, pdf_meta)
        self.write_document_md(page_md)

        if fig_count > 0:
            catalog = build_images_catalog(doc, figure_map)
            (self.output_dir / "images.md").write_text(catalog)

        if self.options.all_formats:
            self.write_all_formats(doc)
            (self.output_dir / "output_embedded.md").write_text(
                doc.export_to_markdown(image_mode=ImageRefMode.EMBEDDED)
            )

        if title:
            logger.info("Title: %s", title)
        self.print_summary(fig_count=fig_count)
