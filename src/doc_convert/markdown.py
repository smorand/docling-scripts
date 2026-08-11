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
        logger.debug("Could not read PDF title metadata from %s", pdf_path, exc_info=True)
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


def _render_table_lines(
    item: TableItem,
    doc: object,
    ref: str,
    artifacts: FloatingArtifacts,
    ctx: FloatingContext,
) -> list[str]:
    """Heading, inlined markdown table, then its description."""
    lines = [_heading_for(ctx.label, "Table", ctx.caption), ""]
    try:
        table_md = item.export_to_markdown(doc=doc).strip()  # type: ignore[arg-type]
    except Exception:
        logger.warning("Failed to serialise table %s", ref)
        table_md = ""
    if table_md:
        lines.extend([table_md, ""])
    block = _format_description_block(artifacts.table_descriptions.get(ref, ""), ctx.mention)
    if block:
        lines.extend([block, ""])
    return lines


def _render_figure_lines(
    ref: str,
    artifacts: FloatingArtifacts,
    ctx: FloatingContext,
    seen_figures: dict[str, str] | None,
    location: str,
) -> list[str]:
    """Heading, description (or a pointer to it), then the image link.

    ``seen_figures`` maps an already-described figure path to the ``location``
    where its description was printed. A template image reused on 36 slides used
    to print the same 200-word description 36 times, which was 24% of a real
    684 KB document.md. Later occurrences keep their heading, their own citing
    sentence (which differs per occurrence) and the image link, and point at the
    first description instead of repeating it. Pass ``None`` to print every
    description in full.
    """
    fig_path = artifacts.figure_paths.get(ref, "")
    if not fig_path:
        # Dropped by the caption filter (size floor) or extraction failed:
        # nothing to show, the figure disappears from document.md entirely.
        return []

    lines = [_heading_for(ctx.label, "Figure", ctx.caption), ""]
    description = artifacts.figure_descriptions.get(ref, "")
    already_at = None if seen_figures is None else seen_figures.get(fig_path)

    if already_at is None:
        block = _format_description_block(description, ctx.mention)
        if block:
            lines.extend([block, ""])
        if seen_figures is not None and description:
            seen_figures[fig_path] = location
    else:
        where = f"under {already_at}" if already_at else "earlier in this document"
        lines.extend([_format_description_block(f"*Same image as {where}; description given there.*", ctx.mention), ""])

    lines.extend([f"*Image: [`{fig_path}`]({fig_path})*", ""])
    return lines


def _render_text_lines(item: object, heading_offset: int) -> list[str]:
    """Section headers, list items, and plain paragraphs."""
    text = getattr(item, "text", None)
    if not text:
        return []
    label = str(getattr(item, "label", "")).lower()
    if "section_header" in label:
        level = getattr(item, "level", 1)
        return [f"{'#' * min(level + heading_offset, 6)} {text}\n"]
    if "list_item" in label:
        return [f"- {text}"]
    return [f"{text}\n"]


def _render_item_lines(
    item: object,
    doc: object,
    ref: str,
    artifacts: FloatingArtifacts,
    contexts: dict[str, FloatingContext],
    *,
    heading_offset: int = 1,
    seen_figures: dict[str, str] | None = None,
    location: str = "",
) -> list[str]:
    """Render one docling item (table, figure, or text) as markdown lines.

    ``heading_offset`` shifts section-header levels so they nest correctly under
    whatever heading structure the caller already emitted (e.g. PPTX per-slide
    grouping nests two levels deeper than the flat PDF/DOCX output).
    """
    if isinstance(item, TableItem):
        return _render_table_lines(item, doc, ref, artifacts, contexts.get(ref, FloatingContext()))
    if isinstance(item, PictureItem):
        return _render_figure_lines(ref, artifacts, contexts.get(ref, FloatingContext()), seen_figures, location)
    return _render_text_lines(item, heading_offset)


def build_document_markdown(
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
    seen_figures: dict[str, str] = {}

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
        lines.extend(
            _render_item_lines(
                item,
                doc,
                ref,
                artifacts,
                contexts,
                seen_figures=seen_figures,
                location=f"Page {current_page}" if current_page is not None else "",
            )
        )

    return "\n".join(lines)


def build_pptx_slides_markdown(
    doc: object,
    artifacts: FloatingArtifacts,
    contexts: dict[str, FloatingContext] | None,
    *,
    visual_by_slide: dict[int, str],
    notes_by_slide: dict[int, str],
    slide_count: int,
    title: str = "",
    hidden_slides: set[int] | None = None,
) -> str:
    """Build ``document.md`` for PPTX with 3 sections per slide.

    Each slide gets: (1) extracted text + figures (native docling parse,
    same as other converters), (2) a full-slide screenshot interpretation
    from a vision LLM, (3) speaker notes when present. This lets a
    downstream LLM tell "mechanically extracted content" apart from
    "what a vision model saw on the rendered slide".

    ``hidden_slides`` marks slide numbers hidden in the source PowerPoint
    (still fully processed, just annotated so a reader knows it would not
    appear in an actual presentation).
    """
    hidden_slides = hidden_slides or set()
    contexts = contexts or {}
    lines: list[str] = []
    seen_figures: dict[str, str] = {}
    if title:
        lines.append(f"# {title}\n")

    items_by_slide: dict[int, list[tuple[str, object]]] = {}
    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        prov = getattr(item, "prov", None)
        slide_no = prov[0].page_no if prov else 0
        items_by_slide.setdefault(slide_no, []).append((getattr(item, "self_ref", ""), item))

    max_slide = max([slide_count, *items_by_slide.keys(), *visual_by_slide.keys()], default=0)

    for slide_no in range(1, max_slide + 1):
        suffix = " *(hidden in source presentation)*" if slide_no in hidden_slides else ""
        lines.append(f"## Slide {slide_no}{suffix}\n")

        lines.append("### Extracted Content (text + figures)\n")
        rendered: list[str] = []
        for ref, item in items_by_slide.get(slide_no, []):
            rendered.extend(
                _render_item_lines(
                    item,
                    doc,
                    ref,
                    artifacts,
                    contexts,
                    heading_offset=3,
                    seen_figures=seen_figures,
                    location=f"Slide {slide_no}",
                )
            )
        # A slide can have items that render to nothing: every figure on it was
        # dropped by the caption filter, or extraction failed. Emit the
        # placeholder rather than an ambiguous empty section.
        if any(line.strip() for line in rendered):
            lines.extend(rendered)
        else:
            lines.append("_No extractable text content on this slide._")
        lines.append("")

        lines.append("### Visual Interpretation (full slide screenshot)\n")
        visual = visual_by_slide.get(slide_no, "").strip()
        lines.append(visual if visual else "_No visual interpretation available._")
        lines.append("")

        lines.append("### Speaker Notes\n")
        notes = notes_by_slide.get(slide_no, "").strip()
        lines.append(notes if notes else "_No speaker notes._")
        lines.append("")
        lines.append("---\n")

    return "\n".join(lines)


# ─── Sidecar image catalog (images.md) ──────────────────────────────────────


@dataclass
class _CatalogEntry:
    """One distinct image file in ``images.md``, with every placement it has."""

    heading: str
    classification: str
    description: str
    pages: list[str] = field(default_factory=list)
    captions: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)


def _collect_catalog_entries(
    doc: object,
    artifacts: FloatingArtifacts,
    contexts: dict[str, FloatingContext],
) -> dict[str, _CatalogEntry]:
    """Group picture items by image file, merging the context of each placement."""
    entries: dict[str, _CatalogEntry] = {}
    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        if not isinstance(item, PictureItem):
            continue
        ref = getattr(item, "self_ref", "")
        fig_path = artifacts.figure_paths.get(ref, "")
        if not fig_path:
            continue

        ctx = contexts.get(ref, FloatingContext())
        description = (artifacts.figure_descriptions.get(ref, "") or get_vlm_description(item)).strip()
        classification = get_picture_classification(item)
        prov = getattr(item, "prov", None)
        page_no = str(prov[0].page_no) if prov else ""

        entry = entries.setdefault(
            fig_path,
            _CatalogEntry(
                heading=ctx.label or Path(fig_path).stem,
                classification=classification,
                description=description,
            ),
        )
        # A later placement can carry context the first one lacked, so keep
        # merging even once the description is settled.
        for value, bucket in ((page_no, entry.pages), (ctx.caption, entry.captions), (ctx.mention, entry.mentions)):
            if value and value not in bucket:
                bucket.append(value)
        entry.classification = entry.classification or classification
        entry.description = entry.description or description
    return entries


def build_images_catalog(
    doc: object,
    artifacts: FloatingArtifacts,
    contexts: dict[str, FloatingContext] | None = None,
) -> str:
    """Build the standalone ``images.md`` sidecar catalog.

    One entry per distinct image file, not per placement. A template image reused
    on 36 slides produced 36 identical entries, which made 55% of a real 313 KB
    catalog; a catalog that lists the same picture 36 times is not a catalog. Each
    entry records every page or slide the image appears on, and every caption and
    citing sentence collected across those placements.
    """
    entries = _collect_catalog_entries(doc, artifacts, contexts or {})

    lines: list[str] = ["# Image Catalog\n"]
    for fig_path, entry in entries.items():
        lines.append(f"## {entry.heading}")
        lines.append("")
        lines.append(f"![{Path(fig_path).stem}]({fig_path})")
        lines.append("")
        lines.append(f"- **Path:** `{fig_path}`")
        if entry.pages:
            lines.append(f"- **{'Pages' if len(entry.pages) > 1 else 'Page'}:** {', '.join(entry.pages)}")
        if entry.classification:
            lines.append(f"- **Type:** {entry.classification}")
        lines.extend(f"- **Caption:** {caption}" for caption in entry.captions)
        lines.extend(f"- **Mention:** {mention}" for mention in entry.mentions)
        lines.append("")
        lines.append("### Description")
        lines.append("")
        lines.append(entry.description or "_No description available._")
        lines.append("")

    return "\n".join(lines)
