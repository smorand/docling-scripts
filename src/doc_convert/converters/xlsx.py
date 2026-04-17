"""XLSX converter: Docling native export."""

from __future__ import annotations

import logging

from doc_convert.base import BaseConverter

logger = logging.getLogger(__name__)


class XlsxConverter(BaseConverter):
    """XLSX conversion: simple Docling export to directory output."""

    def convert(self) -> None:
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.document_converter import DocumentConverter, ExcelFormatOption  # noqa: PLC0415

        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()

        converter = DocumentConverter(
            allowed_formats=[InputFormat.XLSX],
            format_options={InputFormat.XLSX: ExcelFormatOption()},
        )

        with trace_span("docling.convert_xlsx", file=self.source.name):
            logger.info("Converting %s (XLSX native)", self.source.name)
            result = converter.convert(str(self.source))
            logger.info("Status: %s", result.status)

        doc = result.document
        self.write_document_md(doc.export_to_markdown())

        if self.options.all_formats:
            self.write_all_formats(doc)

        self.print_summary()
