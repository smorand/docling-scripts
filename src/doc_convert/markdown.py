"""Shared markdown building for document converters.

Used by PDF, DOCX, and PPTX converters.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pypdfium2
from docling_core.types.doc.document import PictureItem, TableItem
from docling_core.types.doc.labels import DocItemLabel

logger = logging.getLogger(__name__)


def get_document_title(doc: object, pdf_path: str) -> str:
    """Extract document title from docling document or PDF metadata."""
    for t in doc.texts:  # type: ignore[attr-defined]
        if t.label == DocItemLabel.TITLE:
            return t.text  # type: ignore[no-any-return]
    try:
        pdf = pypdfium2.PdfDocument(pdf_path)
        meta = pdf.get_metadata_dict()
        title = meta.get("Title", "")
        if title:
            return title  # type: ignore[no-any-return]
    except Exception:
        pass
    return ""


def get_pdf_metadata(pdf_path: str) -> dict[str, str]:
    """Extract metadata from PDF file."""
    meta: dict[str, str] = {}
    try:
        pdf = pypdfium2.PdfDocument(pdf_path)
        raw = pdf.get_metadata_dict()
        for key in ("Author", "Subject", "Keywords", "Creator", "CreationDate"):
            val = raw.get(key, "")
            if val:
                meta[key] = val
        meta["Pages"] = str(len(pdf))
        meta["File"] = Path(pdf_path).name
    except Exception:
        meta["File"] = Path(pdf_path).name
    return meta


def get_vlm_description(item: PictureItem) -> str:
    """Extract VLM-generated description from item metadata."""
    meta = getattr(item, "meta", None)
    if meta is None:
        return ""
    desc = getattr(meta, "description", None)
    if desc is None:
        return ""
    return getattr(desc, "text", "") or ""


def get_picture_classification(item: PictureItem) -> str:
    """Extract picture classification label."""
    meta = getattr(item, "meta", None)
    if meta is None:
        return ""
    cls_info = getattr(meta, "classification", None)
    if cls_info is None:
        return ""
    return getattr(cls_info, "label", "") or ""


def extract_title(doc: object) -> str:
    """Extract title from Docling document text items."""
    for t in doc.texts:  # type: ignore[attr-defined]
        if t.label == DocItemLabel.TITLE:
            return t.text  # type: ignore[no-any-return]
    return ""


def build_page_annotated_markdown(  # noqa: PLR0912
    doc: object,
    figure_map: dict[str, str],
    title: str = "",
    pdf_meta: dict[str, str] | None = None,
    figure_descriptions: dict[str, str] | None = None,  # noqa: ARG001 — kept for caller symmetry
) -> str:
    """Build markdown with page number annotations.

    Figure descriptions are intentionally NOT inlined here; they live in
    ``images.md`` (produced by :func:`build_images_catalog`). The parameter
    is accepted for caller symmetry.
    """
    lines: list[str] = []
    current_page = None

    if title:
        lines.append(f"# {title}\n")

    if pdf_meta:
        lines.append("| | |")
        lines.append("|---|---|")
        for key, val in pdf_meta.items():
            lines.append(f"| **{key}** | {val} |")
        lines.append("")

    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        prov = getattr(item, "prov", None)
        if prov:
            page_no = prov[0].page_no
            if page_no != current_page:
                if current_page is not None:
                    lines.append("")
                lines.append(f"---\n*[Page {page_no}]*\n")
                current_page = page_no

        if isinstance(item, TableItem):
            df = item.export_to_dataframe(doc)
            lines.append(df.to_markdown(index=False))
            caption = item.caption_text(doc)
            if caption:
                lines.append(f"\n*{caption}*")
            lines.append("")
        elif isinstance(item, PictureItem):
            caption = item.caption_text(doc)
            fig_path = figure_map.get(item.self_ref, "")

            lines.append(f"[Figure: {caption}]" if caption else "[Figure]")
            if fig_path:
                lines.append(f"![figure]({fig_path})")
            lines.append("")
        else:
            text = getattr(item, "text", None)
            if text:
                label = getattr(item, "label", "")
                if "section_header" in str(label).lower():
                    level = getattr(item, "level", 1)
                    prefix = "#" * min(level + 1, 6)
                    lines.append(f"{prefix} {text}\n")
                elif "list_item" in str(label).lower():
                    lines.append(f"- {text}")
                else:
                    lines.append(f"{text}\n")

    return "\n".join(lines)


def build_images_catalog(
    doc: object,
    figure_map: dict[str, str],
    figure_descriptions: dict[str, str] | None = None,
) -> str:
    """Build an images.md catalog with detailed descriptions.

    Descriptions come from the ``figure_descriptions`` map (produced by
    ``BaseConverter.describe_figures``). For backward compatibility, falls back
    to the Docling in-pipeline annotation when the map is missing.
    """
    lines: list[str] = ["# Image Catalog\n"]
    descriptions = figure_descriptions or {}

    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        if not isinstance(item, PictureItem):
            continue
        fig_path = figure_map.get(item.self_ref, "")
        if not fig_path:
            continue

        description = descriptions.get(item.self_ref, "") or get_vlm_description(item)
        caption = item.caption_text(doc)
        classification = get_picture_classification(item)
        page_no = ""
        prov = getattr(item, "prov", None)
        if prov:
            page_no = str(prov[0].page_no)

        lines.append(f"## {Path(fig_path).name}")
        lines.append("")
        lines.append(f"![{Path(fig_path).stem}]({fig_path})")
        lines.append("")
        lines.append(f"- **Path:** `{fig_path}`")
        if page_no:
            lines.append(f"- **Page:** {page_no}")
        if classification:
            lines.append(f"- **Type:** {classification}")
        if caption:
            lines.append(f"- **Caption:** {caption}")
        lines.append("")
        lines.append("### Description")
        lines.append("")
        lines.append(description.strip() if description else "_No description available._")
        lines.append("")

    return "\n".join(lines)
