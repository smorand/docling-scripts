"""EML converter: html2text-rendered body (links preserved, layout tables
flattened) + recursive sub-conversion of attachments under ``attachments/``.
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

    from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)


def _format_addresses(addresses: list[tuple[str, str]] | None) -> str:
    if not addresses:
        return ""
    formatted: list[str] = []
    for name, email in addresses:
        formatted.append(f"{name} <{email}>" if name else email)
    return ", ".join(formatted)


# Remote image srcs that are pure chrome/tracking, never content: spacer gifs,
# 1x1 pixels, and Marketo/CDN open-tracking beacons. Fetching these would leak
# an "email opened" signal, so we never download remote images at all; we only
# decide here which <img> placeholders to drop entirely from the markdown.
_TRACKING_IMG_HINTS = ("spacer", "trk?", "/open?", "1x1", "pixel")

# Block-level tags that never appear inside a genuine data-table cell; their
# presence marks a <table> as a layout container to be unwrapped.
_BLOCK_TAGS = ("p", "div", "table", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote", "hr")


def _own_cells(table: Tag) -> list[Tag]:
    """Cells (td/th) belonging directly to ``table``, excluding nested tables' cells."""
    from bs4 import Tag as _Tag  # noqa: PLC0415

    return [c for c in table.find_all(["td", "th"]) if isinstance(c, _Tag) and c.find_parent("table") is table]


def _is_layout_table(table: Tag) -> bool:
    """Heuristic: is this <table> used for page layout (unwrap) vs real data (keep)?

    Layout tables are the scaffolding of HTML emails. We keep a table only when it
    looks tabular: header cells, no nested table, inline-only cells, and at least
    2 rows by 2 columns. Everything else is unwrapped to plain blocks.
    """
    from bs4 import Tag as _Tag  # noqa: PLC0415

    if str(table.get("role") or "").lower() in {"presentation", "none"}:
        return True
    cells = _own_cells(table)
    if any(c.name == "th" for c in cells):
        return False  # header cells -> genuine data table
    if table.find("table"):
        return True  # nested table -> layout container
    if any(cell.find(_BLOCK_TAGS) for cell in cells):
        return True  # block-level content in cells -> layout
    rows = [r for r in table.find_all("tr") if isinstance(r, _Tag) and r.find_parent("table") is table]
    max_cols = max((len(r.find_all(["td", "th"], recursive=False)) for r in rows), default=0)
    return len(rows) <= 1 or max_cols <= 1  # single row or single column -> layout


def _flatten_layout_tables(soup: BeautifulSoup) -> None:
    """Replace every layout <table> with the plain blocks of its cells, in place.

    Processed deepest-first so nested layout tables resolve before their parents;
    genuine data tables are left untouched and html2text renders them as proper
    markdown tables.
    """
    from bs4 import Tag as _Tag  # noqa: PLC0415

    tables = [t for t in soup.find_all("table") if isinstance(t, _Tag)]
    tables.sort(key=lambda t: len(t.find_parents("table")), reverse=True)
    for table in tables:
        if not _is_layout_table(table):
            continue
        container = soup.new_tag("div")
        for cell in _own_cells(table):
            block = soup.new_tag("div")
            for child in list(cell.contents):
                block.append(child.extract())
            container.append(block)
        table.replace_with(container)


def _clean_email_html(html: str) -> str:
    """Strip layout/tracking noise from email HTML before markdown conversion.

    Email HTML is layout-table soup with tracking pixels, spacer gifs and empty
    tracking anchors. We drop those and unwrap layout tables so the downstream
    text-first converter (html2text) yields clean paragraphs, while genuine data
    tables survive as markdown tables.
    """
    from bs4 import BeautifulSoup, Tag  # noqa: PLC0415

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        src = str(img.get("src") or "").lower()
        width, height = str(img.get("width") or ""), str(img.get("height") or "")
        if any(hint in src for hint in _TRACKING_IMG_HINTS) or width in {"0", "1"} or height in {"0", "1"}:
            img.decompose()
    # Anchors with no visible text (tracking beacons, or logo links — images are
    # rendered off downstream) render as noisy "[ ](url)"; drop them.
    for anchor in soup.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        if not anchor.get_text(strip=True):
            anchor.decompose()
    _flatten_layout_tables(soup)
    return str(soup)


def _bullets_from_escaped_dashes(markdown: str) -> str:
    r"""Turn html2text's escaped literal dashes ("\- text") into real markdown bullets.

    Emails use "- " prefixed lines as visual bullets; html2text escapes the dash to
    keep them as text. We restore them as a tight markdown list (no blank line
    between consecutive items).
    """
    markdown = re.sub(r"(?m)^\\- ", "- ", markdown)
    # Tighten: drop the blank line html2text left between consecutive bullet items.
    while True:
        tightened = re.sub(r"(?m)^(- .*)\n\n(?=- )", r"\1\n", markdown)
        if tightened == markdown:
            return tightened
        markdown = tightened


def _html_to_markdown(html: str) -> str:
    """Convert email HTML body to clean markdown (links + data tables preserved).

    Pure function (no mail/IO state) so it is unit-testable. Uses html2text rather
    than Docling's HTMLDocumentBackend, which collapsed the nested layout tables of
    a typical marketing/HR email into a single unreadable markdown table cell and
    dropped every link URL. Layout tables are unwrapped upstream in
    :func:`_clean_email_html`; genuine data tables render as markdown tables here.
    """
    import html2text  # noqa: PLC0415

    converter = html2text.HTML2Text()
    converter.ignore_images = True
    converter.body_width = 0  # never hard-wrap paragraphs
    converter.pad_tables = True  # emit valid, aligned markdown tables for data tables
    markdown = converter.handle(_clean_email_html(html))

    # Post-process: drop zero-width/invisible filler some clients inject for
    # spacing, then collapse the runs of blank lines html2text leaves behind.
    markdown = markdown.replace("\u200c", " ").replace("\u200b", "")  # ZWNJ -> space, ZWSP -> drop
    markdown = re.sub(r"\*\*\s*\*\*", "", markdown)  # empty <strong></strong> leftovers
    markdown = _bullets_from_escaped_dashes(markdown)
    markdown = re.sub(r"[ \t]+\n", "\n", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    return markdown.strip()


def _email_to_markdown_body(mail: mailparser.MailParser) -> str:
    """Return a markdown rendering of the email body.

    Converts the HTML part with html2text (links + paragraphs preserved) when
    present, falls back to the plain-text body otherwise.
    """
    if mail.text_html:
        return _html_to_markdown("\n".join(mail.text_html))
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
    """EML conversion: header table + html2text-rendered body + recursively converted attachments."""

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
