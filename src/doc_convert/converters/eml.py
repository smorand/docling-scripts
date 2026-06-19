"""EML converter: Docling EmailDocumentBackend for the body + recursive
sub-conversion of attachments under ``attachments/``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

import mailparser

from doc_convert.base import BaseConverter
from doc_convert.output import print_output_summary
from doc_convert.recursive import build_attachments_section, convert_children

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def _format_addresses(addresses: list[tuple[str, str]] | None) -> str:
    if not addresses:
        return ""
    formatted: list[str] = []
    for name, email in addresses:
        formatted.append(f"{name} <{email}>" if name else email)
    return ", ".join(formatted)


def _email_to_markdown_body(mail: mailparser.MailParser) -> str:
    """Return a markdown rendering of the email body.

    Uses Docling's HTMLDocumentBackend when an HTML part exists (proper handling
    of tables, lists, images), falls back to the plain-text body otherwise.
    """
    if mail.text_html:
        from io import BytesIO  # noqa: PLC0415

        from docling.backend.html_backend import HTMLDocumentBackend  # noqa: PLC0415
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.datamodel.document import InputDocument  # noqa: PLC0415

        html = "\n".join(mail.text_html)
        stream = BytesIO(html.encode("utf-8"))
        in_doc = InputDocument(
            path_or_stream=stream,
            format=InputFormat.HTML,
            filename="email-body.html",
            backend=HTMLDocumentBackend,
        )
        backend = HTMLDocumentBackend(in_doc=in_doc, path_or_stream=stream)
        doc = backend.convert()
        return doc.export_to_markdown().strip()
    if mail.text_plain:
        return "\n\n".join(p.strip() for p in mail.text_plain if p.strip())
    return ""


_UNSAFE_FILENAME_CHARS = re.compile(r"[\x00-\x1f/\\]")


def _sanitize_filename(raw: str, fallback: str) -> str:
    """Strip path separators and control chars (incl. null bytes from MSG headers)."""
    cleaned = _UNSAFE_FILENAME_CHARS.sub("", raw or "").strip(" .")
    return cleaned or fallback


def _extract_attachments(mail: mailparser.MailParser, attachments_dir: Path) -> list[Path]:
    """Write each attachment to disk under ``attachments_dir`` and return the paths."""
    paths: list[Path] = []
    attachments_dir.mkdir(parents=True, exist_ok=True)
    for i, att in enumerate(mail.attachments):
        filename = _sanitize_filename(att.get("filename", ""), f"attachment_{i}")
        payload = att.get("payload", "")
        binary = att.get("binary", False)
        try:
            if binary:
                import base64  # noqa: PLC0415

                data = base64.b64decode(payload)
            else:
                charset = att.get("mail_content_type_charset") or "utf-8"
                data = payload.encode(charset, errors="replace") if isinstance(payload, str) else payload
        except Exception as exc:
            logger.warning("Failed to decode attachment %s: %s", filename, exc)
            continue
        out_path = attachments_dir / filename
        out_path.write_bytes(data)
        paths.append(out_path)
    return paths


class EmlConverter(BaseConverter):
    """EML conversion: header table + Docling-rendered body + recursively converted attachments."""

    def convert(self) -> None:
        logger.info("Converting %s (EML)", self.source.name)
        self.ensure_output_dir()

        mail = mailparser.parse_from_file(str(self.source))

        subject = mail.subject or "(no subject)"
        lines: list[str] = [f"# {subject}\n"]
        lines.append("| | |")
        lines.append("|---|---|")
        if from_ := _format_addresses(mail.from_):
            lines.append(f"| **From** | {from_} |")
        if to := _format_addresses(mail.to):
            lines.append(f"| **To** | {to} |")
        if cc := _format_addresses(mail.cc):
            lines.append(f"| **Cc** | {cc} |")
        if mail.date:
            lines.append(f"| **Date** | {mail.date.isoformat()} |")
        if mail.message_id:
            lines.append(f"| **Message-ID** | {mail.message_id} |")
        lines.append("")

        body = _email_to_markdown_body(mail)
        if body:
            lines.append(body)
            lines.append("")

        attachments_dir = self.output_dir / "attachments"
        attachment_paths = _extract_attachments(mail, attachments_dir) if mail.attachments else []

        entries = []
        if attachment_paths:
            entries = convert_children(
                attachment_paths,
                self.options.settings,
                llm=self.options.llm,
            )
            section = build_attachments_section(entries)
            if section:
                lines.append(section)

        self.write_document_md("\n".join(lines))

        print_output_summary(
            self.output_dir,
            fig_count=len(attachment_paths),
            all_formats=self.options.all_formats,
            extra_files=["attachments/ (recursively converted)"] if attachment_paths else None,
        )
