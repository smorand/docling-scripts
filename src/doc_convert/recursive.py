"""Recursive ``doc-convert`` helper for sub-documents (email attachments, audio companions).

The parent converter saves binary children into a directory (e.g. ``attachments/``
for email, ``additional_documents/`` for audio) and asks this module to run
the right child converter on each, producing ``<name>_docling/document.md``
alongside the binary. The returned :class:`AttachmentEntry` list is what the
parent then injects into its top-level ``document.md``.

Failures are non-fatal: an unsupported file or a converter crash yields an
entry whose ``document_md`` is empty so the parent can still mention the file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from doc_convert.base import ConvertOptions
from doc_convert.formats import EXT_TO_FORMAT, resolve_captions
from doc_convert.output import make_document_symlink

if TYPE_CHECKING:
    from pathlib import Path

    from config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AttachmentEntry:
    """Result of recursively converting one child file."""

    source: Path
    output_dir: Path
    document_md: str
    converted: bool


def _supported(path: Path) -> bool:
    return path.suffix.lower() in EXT_TO_FORMAT or path.suffix.lower() in {".eml", ".msg"}


def convert_children(
    paths: list[Path],
    settings: Settings,
    *,
    llm: str | None = None,
    captions: str | None = None,
) -> list[AttachmentEntry]:
    """Recursively convert each child file in place.

    For each child saved at ``<dir>/<basename>``, the conversion output lands
    in ``<dir>/<basename>_docling/``. Returns one :class:`AttachmentEntry`
    per input path (preserves order).
    """
    entries: list[AttachmentEntry] = []
    captions_spec = resolve_captions(captions, llm, settings)

    for path in paths:
        if not path.exists():
            logger.warning("Recursive convert: missing %s", path)
            entries.append(AttachmentEntry(path, path.parent, "", converted=False))
            continue

        out_dir = path.parent / f"{path.name}_docling"
        if not _supported(path):
            logger.info("Recursive convert: %s (unsupported, listed as-is)", path.name)
            entries.append(AttachmentEntry(path, out_dir, "", converted=False))
            continue

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            options = ConvertOptions(
                output_dir=out_dir,
                captions=captions_spec,
                llm=llm,
                settings=settings,
            )
            converter = _build_converter(path, options, llm)
            converter.convert()
            make_document_symlink(out_dir, symlink=False)
            doc_md_path = out_dir / "document.md"
            content = doc_md_path.read_text() if doc_md_path.exists() else ""
            entries.append(AttachmentEntry(path, out_dir, content, converted=bool(content)))
        except Exception as exc:
            logger.warning("Recursive convert failed for %s: %s", path.name, exc)
            entries.append(AttachmentEntry(path, out_dir, "", converted=False))

    return entries


def _build_converter(path: Path, options: ConvertOptions, llm: str | None):  # type: ignore[no-untyped-def]  # noqa: PLR0911
    """Pick the right converter for a child path. Mirrors cli._dispatch logic
    but only for the file-based conversion paths (no audio/video here).
    """
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

    from doc_convert.formats import detect_format  # noqa: PLC0415

    ext = path.suffix.lower()
    if ext == ".eml":
        from doc_convert.converters.eml import EmlConverter  # noqa: PLC0415

        return EmlConverter(path, options)
    if ext == ".msg":
        from doc_convert.converters.msg import MsgConverter  # noqa: PLC0415

        return MsgConverter(path, options)

    fmt = detect_format(path)
    if fmt == InputFormat.IMAGE:
        if not llm:
            raise RuntimeError(f"Image attachment {path.name} requires --llm to describe")
        from doc_convert.converters.image import ImageConverter  # noqa: PLC0415

        return ImageConverter(path, options)
    if fmt == InputFormat.PDF:
        from doc_convert.converters.pdf import PdfConverter  # noqa: PLC0415

        return PdfConverter(path, options)
    if fmt == InputFormat.DOCX:
        from doc_convert.converters.docx import DocxConverter  # noqa: PLC0415

        return DocxConverter(path, options)
    if fmt == InputFormat.PPTX:
        from doc_convert.converters.pptx import PptxConverter  # noqa: PLC0415

        return PptxConverter(path, options)
    if fmt == InputFormat.XLSX:
        from doc_convert.converters.xlsx import XlsxConverter  # noqa: PLC0415

        return XlsxConverter(path, options)
    raise RuntimeError(f"No recursive converter for format {fmt}")


def build_attachments_section(entries: list[AttachmentEntry], heading: str = "Attachments") -> str:
    """Build the ``## {heading}`` markdown block from converted entries."""
    if not entries:
        return ""
    parts: list[str] = [f"## {heading}", ""]
    for entry in entries:
        rel_dir = entry.output_dir.name
        parts.append(f"### {entry.source.name}")
        parts.append("")
        if entry.converted and entry.document_md.strip():
            parts.append(f"*Source: [`{rel_dir}/document.md`]({rel_dir}/document.md)*")
            parts.append("")
            parts.append(entry.document_md.strip())
        else:
            parts.append(f"*Not converted. Binary stored at [`{entry.source.name}`]({entry.source.name}).*")
        parts.append("")
    return "\n".join(parts)
