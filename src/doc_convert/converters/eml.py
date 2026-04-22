"""EML converter: parse email files to structured markdown."""

from __future__ import annotations

import email
import email.policy
import logging
import re

from doc_convert.base import BaseConverter
from doc_convert.output import print_output_summary

logger = logging.getLogger(__name__)


def _html_to_text(html: str) -> str:
    """Simple HTML to markdown conversion (no external dependency)."""
    text = html
    # Remove style/script blocks
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Convert common tags
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"<h([1-6])[^>]*>(.*?)</h\1>",
        lambda m: f"\n{'#' * int(m.group(1))} {m.group(2)}\n",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", r"[\2](\1)", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE | re.DOTALL)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    # Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_body_and_attachments(msg: object) -> tuple[str, str, list[tuple[str, bytes]]]:
    """Extract text body, HTML body, and attachments from an email message."""
    body_text = ""
    body_html = ""
    attachments: list[tuple[str, bytes]] = []

    if msg.is_multipart():  # type: ignore[attr-defined]
        for part in msg.walk():  # type: ignore[attr-defined]
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))

            if "attachment" in disposition:
                filename = part.get_filename() or f"attachment_{len(attachments)}"
                payload = part.get_payload(decode=True)
                if payload:
                    attachments.append((filename, payload))
            elif content_type == "text/plain" and not body_text:
                payload = part.get_payload(decode=True)
                if payload:
                    body_text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            elif content_type == "text/html" and not body_html:
                payload = part.get_payload(decode=True)
                if payload:
                    body_html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        content_type = msg.get_content_type()  # type: ignore[attr-defined]
        payload = msg.get_payload(decode=True)  # type: ignore[attr-defined]
        if payload:
            charset = msg.get_content_charset() or "utf-8"  # type: ignore[attr-defined]
            if content_type == "text/html":
                body_html = payload.decode(charset, errors="replace")
            else:
                body_text = payload.decode(charset, errors="replace")

    return body_text, body_html, attachments


class EmlConverter(BaseConverter):
    """EML (email) conversion to structured markdown."""

    def convert(self) -> None:
        logger.info("Converting %s (EML)", self.source.name)
        self.ensure_output_dir()

        raw = self.source.read_bytes()
        msg = email.message_from_bytes(raw, policy=email.policy.default)

        subject = msg.get("Subject", "(no subject)")
        lines: list[str] = [f"# {subject}\n"]
        lines.append("| | |")
        lines.append("|---|---|")
        lines.append(f"| **From** | {msg.get('From', '')} |")
        lines.append(f"| **To** | {msg.get('To', '')} |")
        cc = msg.get("Cc", "")
        if cc:
            lines.append(f"| **Cc** | {cc} |")
        lines.append(f"| **Date** | {msg.get('Date', '')} |")
        lines.append("")

        body_text, body_html, attachments = _extract_body_and_attachments(msg)

        if body_html:
            lines.append(_html_to_text(body_html))
        elif body_text:
            lines.append(body_text)

        if attachments:
            fig_dir = self.output_dir / "figures"
            fig_dir.mkdir(exist_ok=True)
            lines.append("\n\n## Attachments\n")
            for filename, data in attachments:
                (fig_dir / filename).write_bytes(data)
                lines.append(f"- [{filename}](figures/{filename})")
                logger.info("Extracted attachment: %s", filename)

        self.write_document_md("\n".join(lines))
        print_output_summary(
            self.output_dir,
            fig_count=len(attachments),
            all_formats=self.options.all_formats,
            extra_files=["figures/ (attachments)"] if attachments else None,
        )
