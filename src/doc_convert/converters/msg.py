"""MSG (Outlook) converter: decode the MSG into EML bytes and delegate to EmlConverter.

This keeps the email pipeline (header table, body via Docling HTML backend,
recursive attachment conversion) DRY: only the decoding step is MSG-specific.
"""

from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from doc_convert.base import BaseConverter

logger = logging.getLogger(__name__)


def _msg_to_eml_bytes(msg_path: Path) -> bytes:
    """Convert an Outlook .msg file to RFC-822 (.eml) bytes.

    We deliberately do NOT use ``extract_msg.asEmailMessage()``: it does an
    unconditional ``htmlBody.decode('utf-8')`` which breaks on the many MSG
    files where the HTML body is encoded in cp1252 / latin-1, and it also
    iterates raw MIME headers that contain non-UTF8 antispam tokens. Instead
    we build a minimal valid MIME multipart from the few attributes we care
    about (subject, addresses, dates, body, attachments).
    """
    from email.message import EmailMessage  # noqa: PLC0415

    import extract_msg  # noqa: PLC0415

    msg = extract_msg.openMsg(str(msg_path))
    try:
        em = EmailMessage()
        if subject := getattr(msg, "subject", None):
            em["Subject"] = subject
        if sender := getattr(msg, "sender", None):
            em["From"] = sender
        if to := getattr(msg, "to", None):
            em["To"] = to
        if cc := getattr(msg, "cc", None):
            em["Cc"] = cc
        if date := getattr(msg, "date", None):
            em["Date"] = str(date)
        if mid := getattr(msg, "messageId", None):
            em["Message-ID"] = mid

        text_body = getattr(msg, "body", None) or ""
        html_body_raw = getattr(msg, "htmlBody", None)
        em.set_content(text_body or "(no plain text body)")
        if html_body_raw:
            html_text = _decode_lenient(html_body_raw)
            em.add_alternative(html_text, subtype="html")

        for att in getattr(msg, "attachments", []) or []:
            data = getattr(att, "data", None)
            if data is None or not isinstance(data, (bytes, bytearray)):
                continue
            filename = getattr(att, "longFilename", None) or getattr(att, "shortFilename", None) or "attachment.bin"
            em.add_attachment(
                bytes(data),
                maintype="application",
                subtype="octet-stream",
                filename=str(filename),
            )

        return em.as_bytes()
    finally:
        msg.close()


_META_CHARSET_RE = re.compile(rb"(?i)(<meta[^>]+charset=[\"']?)([\w-]+)([\"']?[^>]*>)")


def _decode_lenient(raw: bytes) -> str:
    """Decode an MSG body honouring the embedded ``<meta charset=...>`` first.

    Outlook MSG files can store htmlBody bytes as cp1252, UTF-8, ISO-8859-1, etc.
    When the HTML carries a declared charset we trust it. The decoded str is
    then re-emitted as UTF-8 with the meta tag rewritten to ``charset=utf-8``
    so downstream parsers (mailparser, Docling HTMLDocumentBackend) don't fall
    back to the original charset and produce mojibake.
    """
    import contextlib  # noqa: PLC0415

    declared = _META_CHARSET_RE.search(raw)
    candidates: list[str] = []
    if declared:
        with contextlib.suppress(UnicodeDecodeError):
            candidates.append(declared.group(2).decode("ascii").lower())
    candidates.extend(["utf-8", "cp1252", "latin-1"])

    text = ""
    for codec in candidates:
        try:
            text = raw.decode(codec)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not text:
        text = raw.decode("utf-8", errors="replace")

    # Rewrite charset declaration so the str -> utf-8 re-encoding stays coherent.
    return _META_CHARSET_RE.sub(rb"\1utf-8\3", text.encode("utf-8")).decode("utf-8")


class MsgConverter(BaseConverter):
    """Outlook MSG → EML → EmlConverter delegation."""

    def convert(self) -> None:
        from doc_convert.converters.eml import EmlConverter  # noqa: PLC0415

        logger.info("Converting %s (MSG → EML)", self.source.name)

        eml_bytes = _msg_to_eml_bytes(self.source)
        with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as tmp:
            tmp.write(eml_bytes)
            tmp_path = Path(tmp.name)

        try:
            # output_dir already points at <msg-stem>_docling/. EmlConverter
            # reads the tmp .eml but writes into our output_dir, so the
            # original .msg name is preserved on disk via the parent directory.
            EmlConverter(tmp_path, self.options).convert()
        finally:
            tmp_path.unlink(missing_ok=True)
