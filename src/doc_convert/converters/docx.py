"""DOCX converter: Docling native + image extraction + VLM descriptions."""

from __future__ import annotations

import logging

from doc_convert.base import BaseConverter
from doc_convert.markdown import build_images_catalog, build_page_annotated_markdown, extract_title

logger = logging.getLogger(__name__)


class DocxConverter(BaseConverter):
    """DOCX conversion with image extraction and VLM descriptions."""

    def convert(self) -> None:
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.document_converter import DocumentConverter, WordFormatOption  # noqa: PLC0415

        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()

        converter = DocumentConverter(
            allowed_formats=[InputFormat.DOCX],
            format_options={InputFormat.DOCX: WordFormatOption()},
        )

        with trace_span("docling.convert_docx", file=self.source.name):
            logger.info("Converting %s (DOCX native)", self.source.name)
            result = converter.convert(str(self.source))
            logger.info("Status: %s", result.status)

        doc = result.document

        figure_map: dict[str, str] = {}
        figure_descriptions: dict[str, str] = {}
        fig_count = 0

        if self.options.figures:
            figure_map, image_paths, item_refs = self.extract_figures_from_doc(doc)
            fig_count = len(figure_map)
            figure_descriptions = self.describe_figures(image_paths, item_refs, "vlm.describe_docx_images")

        title = extract_title(doc)
        page_md = build_page_annotated_markdown(doc, figure_map, title, figure_descriptions=figure_descriptions)
        self.write_document_md(page_md)

        if fig_count > 0:
            catalog = build_images_catalog(doc, figure_map, figure_descriptions)
            (self.output_dir / "images.md").write_text(catalog)

        if self.options.all_formats:
            self.write_all_formats(doc)

        if title:
            logger.info("Title: %s", title)
        self.print_summary(
            fig_count=fig_count,
            captions_used=self.options.captions_enabled and fig_count > 0,
            desc_count=len(figure_descriptions),
        )
