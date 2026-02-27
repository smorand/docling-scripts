#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "docling @ file:///Users/sebastien/projects/external/docling",
#     "pypdfium2",
# ]
# ///
"""Convert a PDF to page-annotated markdown with extracted figures.

Usage:
    uv run python scripts/convert_pdf.py input.pdf
    uv run python scripts/convert_pdf.py input.pdf -o /tmp/my-output
    uv run python scripts/convert_pdf.py input.pdf --no-ocr
    uv run python scripts/convert_pdf.py input.pdf --no-vlm
    uv run python scripts/convert_pdf.py input.pdf --vlm-preset granite_vision
    uv run python scripts/convert_pdf.py input.pdf --all
"""

import argparse
import json
import sys
from pathlib import Path

import pypdfium2
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    PictureDescriptionVlmEngineOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode
from docling_core.types.doc.labels import DocItemLabel
from docling_core.types.doc.document import PictureItem, TableItem

VLM_PRESETS = ["smolvlm", "granite_vision", "pixtral", "qwen"]


def convert_pdf(
    pdf_path: str,
    output_dir: str | None = None,
    do_ocr: bool = True,
    vlm: bool = True,
    vlm_preset: str = "smolvlm",
    all_formats: bool = False,
) -> None:
    pdf = Path(pdf_path)
    if not pdf.exists():
        print(f"Error: {pdf} not found", file=sys.stderr)
        sys.exit(1)

    out = Path(output_dir) if output_dir else pdf.parent / f"{pdf.stem}_docling"
    out.mkdir(parents=True, exist_ok=True)

    pipeline_options = PdfPipelineOptions(
        do_ocr=do_ocr,
        do_table_structure=True,
        generate_page_images=True,
        generate_picture_images=True,
        do_picture_description=vlm,
    )

    if vlm:
        pipeline_options.picture_description_options = (
            PictureDescriptionVlmEngineOptions.from_preset(vlm_preset)
        )
        print(
            f"VLM picture description enabled (preset: {vlm_preset})", file=sys.stderr
        )

    converter = DocumentConverter(
        format_options={"pdf": PdfFormatOption(pipeline_options=pipeline_options)}
    )

    print(f"Converting {pdf.name}...", file=sys.stderr)
    result = converter.convert(str(pdf))
    print(f"Status: {result.status}", file=sys.stderr)

    doc = result.document

    # Extract figures (needed for filename references in markdown)
    fig_dir = out / "figures"
    fig_dir.mkdir(exist_ok=True)
    figure_map: dict[str, str] = {}  # self_ref -> filename
    fig_count = 0
    for item, _ in doc.iterate_items():
        if isinstance(item, PictureItem):
            img = item.get_image(doc)
            if img:
                filename = f"figure_{fig_count}.png"
                img.save(fig_dir / filename)
                figure_map[item.self_ref] = f"figures/{filename}"
                fig_count += 1

    # Extract document title and PDF metadata
    title = get_document_title(doc, str(pdf))
    pdf_meta = get_pdf_metadata(str(pdf))

    # Page-annotated markdown (always generated)
    page_md = build_page_annotated_markdown(doc, figure_map, title, pdf_meta)
    (out / "output_pages.md").write_text(page_md)

    # Additional formats (only with --all)
    if all_formats:
        (out / "output.md").write_text(doc.export_to_markdown())
        (out / "output_with_images.md").write_text(
            doc.export_to_markdown(image_mode=ImageRefMode.EMBEDDED)
        )
        (out / "output.json").write_text(
            json.dumps(doc.export_to_dict(), indent=2, default=str)
        )
        (out / "output.txt").write_text(doc.export_to_text())
        (out / "output.html").write_text(doc.export_to_html())

    if title:
        print(f"Title: {title}", file=sys.stderr)
    print(f"Output saved to {out}/", file=sys.stderr)
    print(
        f"  figures/             - {fig_count} extracted figure(s)", file=sys.stderr
    )
    print(f"  output_pages.md      - page-annotated markdown", file=sys.stderr)
    if all_formats:
        print(f"  output.md            - standard markdown", file=sys.stderr)
        print(
            f"  output_with_images.md - markdown with embedded images", file=sys.stderr
        )
        print(f"  output.json          - full structured JSON", file=sys.stderr)
        print(f"  output.txt           - plain text", file=sys.stderr)
        print(f"  output.html          - HTML", file=sys.stderr)


def get_document_title(doc, pdf_path: str) -> str:
    """Extract document title from docling document or PDF metadata as fallback."""
    # 1. Check for a title item in the document
    for t in doc.texts:
        if t.label == DocItemLabel.TITLE:
            return t.text

    # 2. Fallback: read title from PDF metadata
    try:
        pdf = pypdfium2.PdfDocument(pdf_path)
        meta = pdf.get_metadata_dict()
        title = meta.get("Title", "")
        if title:
            return title
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
    """Extract VLM-generated description from item.meta.description."""
    meta = getattr(item, "meta", None)
    if meta is None:
        return ""
    desc = getattr(meta, "description", None)
    if desc is None:
        return ""
    return getattr(desc, "text", "") or ""


def build_page_annotated_markdown(
    doc,
    figure_map: dict[str, str],
    title: str = "",
    pdf_meta: dict[str, str] | None = None,
) -> str:
    """Build markdown with page number annotations before each page break."""
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

    for item, _ in doc.iterate_items():
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
            description = get_vlm_description(item)
            fig_path = figure_map.get(item.self_ref, "")

            # Build figure block
            if caption:
                lines.append(f"[Figure: {caption}]")
            else:
                lines.append("[Figure]")
            if fig_path:
                lines.append(f"![figure]({fig_path})")
            if description:
                lines.append(f"\n> {description}")
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


def main():
    parser = argparse.ArgumentParser(description="Convert PDF using docling")
    parser.add_argument("pdf", help="Path to PDF file")
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument(
        "--no-ocr", action="store_true", help="Disable OCR for scanned pages"
    )
    parser.add_argument(
        "--no-vlm",
        action="store_true",
        help="Disable VLM picture description for figures",
    )
    parser.add_argument(
        "--vlm-preset",
        default="smolvlm",
        choices=VLM_PRESETS,
        help="VLM preset for picture description (default: smolvlm)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Generate all formats (md, html, json, txt, embedded images)",
    )
    args = parser.parse_args()

    convert_pdf(
        pdf_path=args.pdf,
        output_dir=args.output,
        do_ocr=not args.no_ocr,
        vlm=not args.no_vlm,
        vlm_preset=args.vlm_preset,
        all_formats=args.all,
    )


if __name__ == "__main__":
    main()
