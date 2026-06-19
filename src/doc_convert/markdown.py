"""Shared markdown building for document converters.

Used by PDF, DOCX, and PPTX converters. The main entry point is
:func:`build_document_markdown`, which produces a self-contained ``document.md``
with figure and table descriptions inlined right after the corresponding item.
The companion :func:`build_images_catalog` produces a separate ``images.md``
sidecar (full catalog, useful for image-centric RAG).
"""

from __future__ import annotations

import contextlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2
from docling_core.types.doc.document import PictureItem, TableItem
from docling_core.types.doc.labels import DocItemLabel

logger = logging.getLogger(__name__)


# ─── Metadata helpers ──────────────────────────────────────────────────────


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


def extract_title(doc: object) -> str:
    """Extract title from Docling document text items."""
    for t in doc.texts:  # type: ignore[attr-defined]
        if t.label == DocItemLabel.TITLE:
            return t.text  # type: ignore[no-any-return]
    return ""


def get_vlm_description(item: object) -> str:
    """Extract VLM-generated description from item metadata (catalog fallback)."""
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


# ─── Floating context discovery (caption + body mention) ────────────────────


_FLOAT_LABEL_RE = re.compile(
    r"\b(Figure|Fig\.?|Table|Tableau|Schéma|Schema)\s*([0-9]+(?:\.[0-9]+)?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class FloatingContext:
    """Caption and (optional) body-text mention for a floating item.

    ``label`` is e.g. ``"Figure 3"`` when a numbered marker is found, ``""``
    otherwise. ``caption`` is the document's own caption text (may be empty).
    ``mention`` is a body sentence/paragraph that references this float by
    number (e.g. "Figure 3 shows..."). May be empty.
    """

    label: str = ""
    caption: str = ""
    mention: str = ""


_LABEL_NORMALIZE: dict[str, str] = {
    "fig": "Figure",
    "figure": "Figure",
    "table": "Table",
    "tableau": "Tableau",
    "schema": "Schéma",
    "schéma": "Schéma",
}


def _extract_label(caption: str) -> str:
    """Pull ``Figure 3`` out of ``Figure 3: Architecture diagram``."""
    if not caption:
        return ""
    m = _FLOAT_LABEL_RE.search(caption)
    if not m:
        return ""
    kind = m.group(1).rstrip(".").lower()
    return f"{_LABEL_NORMALIZE.get(kind, kind.capitalize())} {m.group(2)}"


def _shorten_sentence(text: str, max_chars: int = 280) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rsplit(" ", 1)[0] + "…"


def _find_mention(body_texts: list[str], number: str, kind_re: str) -> str:
    """Find the first body text that mentions the float by number."""
    pattern = re.compile(rf"\b{kind_re}\s*{re.escape(number)}\b", re.IGNORECASE)
    for text in body_texts:
        if pattern.search(text):
            return _shorten_sentence(text)
    return ""


def collect_floating_contexts(doc: object) -> dict[str, FloatingContext]:
    """Build {self_ref: FloatingContext} for every PictureItem and TableItem.

    Strategy:
        1. First pass collects every text item in reading order.
        2. For each floating item we read its native caption.
        3. If the caption carries a ``Figure N``/``Table N`` marker, we scan
           the body for a sentence that references the same marker.
        4. Otherwise we fall back to the *previous* text item in reading
           order as a weak mention hint.
    """
    body_texts: list[str] = []
    reading_order: list[tuple[str, object]] = []
    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        text = getattr(item, "text", "") or ""
        if text and not isinstance(item, (PictureItem, TableItem)):
            body_texts.append(text)
        reading_order.append((getattr(item, "self_ref", ""), item))

    contexts: dict[str, FloatingContext] = {}
    for idx, (ref, item) in enumerate(reading_order):
        if not isinstance(item, (PictureItem, TableItem)) or not ref:
            continue
        caption = ""
        with contextlib.suppress(Exception):
            caption = item.caption_text(doc)  # type: ignore[arg-type]
        label = _extract_label(caption)
        mention = ""
        if label:
            m = _FLOAT_LABEL_RE.match(label)
            if m:
                kind = m.group(1)
                kind_re = "fig(?:ure|\\.)?" if kind.lower().startswith("fig") else re.escape(kind)
                mention = _find_mention(body_texts, m.group(2), kind_re)
        if not mention:
            for j in range(idx - 1, -1, -1):
                prev = reading_order[j][1]
                prev_text = getattr(prev, "text", "") or ""
                if prev_text and not isinstance(prev, (PictureItem, TableItem)):
                    mention = _shorten_sentence(prev_text)
                    break
        contexts[ref] = FloatingContext(label=label, caption=caption, mention=mention)
    return contexts


# ─── Document markdown builder ─────────────────────────────────────────────


@dataclass
class FloatingArtifacts:
    """Maps figure/table self_refs to their on-disk image path and description."""

    figure_paths: dict[str, str] = field(default_factory=dict)
    figure_descriptions: dict[str, str] = field(default_factory=dict)
    table_descriptions: dict[str, str] = field(default_factory=dict)


def _heading_for(label: str, fallback_kind: str, caption: str) -> str:
    """Build the ``#### Figure N: caption`` heading."""
    if label and caption:
        caption_clean = _FLOAT_LABEL_RE.sub("", caption, count=1).lstrip(" :.-").strip()
        return f"#### {label}: {caption_clean}" if caption_clean else f"#### {label}"
    if label:
        return f"#### {label}"
    if caption:
        return f"#### {fallback_kind}: {caption}"
    return f"#### {fallback_kind}"


def _format_description_block(description: str, mention: str) -> str:
    """Format the description + mention block as a markdown blockquote."""
    if not description and not mention:
        return ""
    lines: list[str] = []
    if description:
        body = description.strip()
        for ln in body.splitlines():
            lines.append(f"> {ln}" if ln else ">")
    if mention:
        if lines:
            lines.append(">")
        lines.append(f"> *Cited in document: «{mention.strip()}»*")
    return "\n".join(lines)


def build_document_markdown(  # noqa: PLR0912, PLR0915
    doc: object,
    artifacts: FloatingArtifacts,
    contexts: dict[str, FloatingContext] | None = None,
    *,
    title: str = "",
    pdf_meta: dict[str, str] | None = None,
    paginated: bool = True,
) -> str:
    """Build a self-contained ``document.md``.

    Figures and tables are emitted with a heading, their VLM description
    (when available), and a link to the on-disk artifact (figures only;
    tables are inlined as markdown directly).
    """
    contexts = contexts or {}
    lines: list[str] = []
    current_page: int | None = None

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
        if paginated and prov:
            page_no = prov[0].page_no
            if page_no != current_page:
                if current_page is not None:
                    lines.append("")
                lines.append(f"---\n*[Page {page_no}]*\n")
                current_page = page_no

        ref = getattr(item, "self_ref", "")
        if isinstance(item, TableItem):
            ctx = contexts.get(ref, FloatingContext())
            lines.append(_heading_for(ctx.label, "Table", ctx.caption))
            lines.append("")
            try:
                table_md = item.export_to_markdown(doc=doc).strip()  # type: ignore[arg-type]
            except Exception:
                logger.warning("Failed to serialise table %s", ref)
                table_md = ""
            if table_md:
                lines.append(table_md)
                lines.append("")
            block = _format_description_block(artifacts.table_descriptions.get(ref, ""), ctx.mention)
            if block:
                lines.append(block)
                lines.append("")
        elif isinstance(item, PictureItem):
            ctx = contexts.get(ref, FloatingContext())
            lines.append(_heading_for(ctx.label, "Figure", ctx.caption))
            lines.append("")
            block = _format_description_block(artifacts.figure_descriptions.get(ref, ""), ctx.mention)
            if block:
                lines.append(block)
                lines.append("")
            fig_path = artifacts.figure_paths.get(ref, "")
            if fig_path:
                lines.append(f"*Image: [`{fig_path}`]({fig_path})*")
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


# ─── Sidecar image catalog (images.md) ──────────────────────────────────────


def build_images_catalog(
    doc: object,
    artifacts: FloatingArtifacts,
    contexts: dict[str, FloatingContext] | None = None,
) -> str:
    """Build the standalone ``images.md`` sidecar catalog."""
    contexts = contexts or {}
    lines: list[str] = ["# Image Catalog\n"]

    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        if not isinstance(item, PictureItem):
            continue
        ref = getattr(item, "self_ref", "")
        fig_path = artifacts.figure_paths.get(ref, "")
        if not fig_path:
            continue

        ctx = contexts.get(ref, FloatingContext())
        description = artifacts.figure_descriptions.get(ref, "") or get_vlm_description(item)
        classification = get_picture_classification(item)
        page_no = ""
        prov = getattr(item, "prov", None)
        if prov:
            page_no = str(prov[0].page_no)

        heading = ctx.label or Path(fig_path).stem
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(f"![{Path(fig_path).stem}]({fig_path})")
        lines.append("")
        lines.append(f"- **Path:** `{fig_path}`")
        if page_no:
            lines.append(f"- **Page:** {page_no}")
        if classification:
            lines.append(f"- **Type:** {classification}")
        if ctx.caption:
            lines.append(f"- **Caption:** {ctx.caption}")
        if ctx.mention:
            lines.append(f"- **Mention:** {ctx.mention}")
        lines.append("")
        lines.append("### Description")
        lines.append("")
        lines.append(description.strip() if description else "_No description available._")
        lines.append("")

    return "\n".join(lines)
