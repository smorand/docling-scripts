"""DOCX converter: Docling native + image extraction + VLM descriptions."""

from __future__ import annotations

import logging

from doc_convert.base import BaseConverter
from doc_convert.markdown import (
    FloatingArtifacts,
    FloatingContext,
    build_document_markdown,
    build_images_catalog,
    collect_floating_contexts,
    extract_title,
)

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
        contexts = collect_floating_contexts(doc)
        context_blocks = _build_context_blocks(contexts)
        artifacts = FloatingArtifacts()

        fig_count = 0
        filtered_count = 0
        if self.options.figures:
            figure_map, image_paths, item_refs = self.extract_figures_from_doc(doc)
            extracted_count = len(item_refs)
            figure_map, image_paths, item_refs = self.filter_figures(figure_map, image_paths, item_refs)
            filtered_count = extracted_count - len(item_refs)
            artifacts.figure_paths = figure_map
            fig_count = len(figure_map)
            artifacts.figure_descriptions = self.describe_figures(
                image_paths, item_refs, "vlm.describe_docx_images", context_blocks
            )

        tbl_count = 0
        if self.options.figures and self.options.captions_enabled:
            tbl_paths, tbl_refs = self.extract_table_images(doc)
            tbl_count = len(tbl_paths)
            artifacts.table_descriptions = self.describe_tables(
                tbl_paths, tbl_refs, "vlm.describe_docx_tables", context_blocks
            )

        title = extract_title(doc)
        page_md = build_document_markdown(doc, artifacts, contexts, title=title, paginated=False)
        self.write_document_md(page_md)

        if fig_count > 0:
            catalog = build_images_catalog(doc, artifacts, contexts)
            (self.output_dir / "images.md").write_text(catalog)

        if self.options.all_formats:
            self.write_all_formats(doc)

        if title:
            logger.info("Title: %s", title)
        self.print_summary(
            fig_count=fig_count,
            captions_used=self.options.captions_enabled and (fig_count > 0 or tbl_count > 0),
            desc_count=len(artifacts.figure_descriptions) + len(artifacts.table_descriptions),
            filtered_count=filtered_count,
        )


def _build_context_blocks(contexts: dict[str, FloatingContext]) -> dict[str, str]:
    from doc_convert.providers import build_context_block  # noqa: PLC0415

    return {ref: build_context_block(caption=ctx.caption, mention=ctx.mention) for ref, ctx in contexts.items()}
