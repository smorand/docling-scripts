"""Unified document converter using Docling.

Converts PDF, images, DOCX, XLSX, and Google Docs/Sheets to markdown.
PDF uses local models by default (full extraction with figures, tables, OCR).
Use --gemini for quick conversion via Gemini API.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from enum import Enum
from pathlib import Path

import httpx
import pypdfium2
import typer
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    PictureDescriptionVlmEngineOptions,
    VlmPipelineOptions,
)
from docling.datamodel.pipeline_options_vlm_model import (
    ApiVlmOptions,
    ResponseFormat,
)
from docling.document_converter import (
    DocumentConverter,
    ExcelFormatOption,
    FormatOption,
    ImageFormatOption,
    PdfFormatOption,
    WordFormatOption,
)
from docling.pipeline.vlm_pipeline import VlmPipeline
from docling_core.types.doc import ImageRefMode
from docling_core.types.doc.document import PictureItem, TableItem
from docling_core.types.doc.labels import DocItemLabel

from config import Settings
from logging_config import console, setup_logging
from tracing import configure_tracing, trace_span

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Convert documents to markdown using Docling.",
    no_args_is_help=True,
)


# ── Enums ─────────────────────────────────────────────────────────────────────


class VlmPreset(str, Enum):
    smolvlm = "smolvlm"
    granite_vision = "granite_vision"
    pixtral = "pixtral"
    qwen = "qwen"


# ── Format detection ──────────────────────────────────────────────────────────

EXT_TO_FORMAT: dict[str, InputFormat] = {
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
    ".xlsx": InputFormat.XLSX,
    ".jpg": InputFormat.IMAGE,
    ".jpeg": InputFormat.IMAGE,
    ".png": InputFormat.IMAGE,
    ".tiff": InputFormat.IMAGE,
    ".tif": InputFormat.IMAGE,
    ".bmp": InputFormat.IMAGE,
    ".webp": InputFormat.IMAGE,
}


def detect_format(path: Path) -> InputFormat:
    """Detect input format from file extension."""
    ext = path.suffix.lower()
    fmt = EXT_TO_FORMAT.get(ext)
    if fmt is None:
        console.print(f"[red]Unsupported extension '{ext}'[/red]")
        console.print(f"Supported: {', '.join(sorted(EXT_TO_FORMAT.keys()))}")
        raise typer.Exit(1)
    return fmt


# ── Google Docs/Sheets support ────────────────────────────────────────────────

GOOGLE_DOC_RE = re.compile(r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")
GOOGLE_SHEET_RE = re.compile(r"https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)")
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DRIVE_EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{file_id}/export"
DRIVE_FILE_URL = "https://www.googleapis.com/drive/v3/files/{file_id}"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def is_google_url(source: str) -> bool:
    """Check if the input is a Google Docs or Sheets URL."""
    return bool(GOOGLE_DOC_RE.search(source) or GOOGLE_SHEET_RE.search(source))


def _load_google_credentials(settings: Settings) -> str:
    """Load Google credentials and return an access token."""
    creds_path = settings.google_credentials
    if not creds_path:
        console.print("[red]GOOGLE_CREDENTIALS env var is required for Google Docs/Sheets[/red]")
        raise typer.Exit(1)

    creds_file = Path(os.path.expandvars(creds_path)).expanduser()
    if not creds_file.exists():
        console.print(f"[red]Credentials file not found: {creds_file}[/red]")
        raise typer.Exit(1)

    from google.oauth2 import credentials as user_credentials  # noqa: PLC0415
    from google.oauth2 import service_account  # noqa: PLC0415

    creds_data = json.loads(creds_file.read_text())

    cred_type = creds_data.get("type", "")
    if cred_type == "service_account":
        creds = service_account.Credentials.from_service_account_file(str(creds_file), scopes=DRIVE_SCOPES)
    elif cred_type == "authorized_user":
        creds = user_credentials.Credentials.from_authorized_user_file(str(creds_file), scopes=DRIVE_SCOPES)
    else:
        console.print(f"[red]Unsupported credential type '{cred_type}'[/red]")
        raise typer.Exit(1)

    from google.auth.transport.requests import Request as AuthRequest  # noqa: PLC0415

    if not creds.valid:
        creds.refresh(AuthRequest())
    return creds.token


def download_google_doc(url: str, settings: Settings) -> tuple[Path, str, InputFormat]:
    """Download a Google Doc/Sheet to a temp file."""
    doc_match = GOOGLE_DOC_RE.search(url)
    sheet_match = GOOGLE_SHEET_RE.search(url)

    if doc_match:
        file_id = doc_match.group(1)
        mime_type, suffix = MIME_DOCX, ".docx"
        fmt, kind = InputFormat.DOCX, "Google Doc"
    elif sheet_match:
        file_id = sheet_match.group(1)
        mime_type, suffix = MIME_XLSX, ".xlsx"
        fmt, kind = InputFormat.XLSX, "Google Sheet"
    else:
        console.print(f"[red]Not a recognized Google Docs/Sheets URL: {url}[/red]")
        raise typer.Exit(1)

    token = _load_google_credentials(settings)
    headers = {"Authorization": f"Bearer {token}"}

    with trace_span("google.download", kind=kind, file_id=file_id), httpx.Client(timeout=60.0) as client:
        title_resp = client.get(
            DRIVE_FILE_URL.format(file_id=file_id),
            headers=headers,
            params={"fields": "name"},
        )
        title = title_resp.json().get("name", file_id) if title_resp.is_success else file_id
        logger.info("Downloading %s: %s", kind, title)

        resp = client.get(
            DRIVE_EXPORT_URL.format(file_id=file_id),
            headers=headers,
            params={"mimeType": mime_type},
        )
        if not resp.is_success:
            console.print(f"[red]Failed to export {kind} (HTTP {resp.status_code})[/red]")
            raise typer.Exit(1)

    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)  # noqa: SIM115
    tmp.write(resp.content)
    tmp.close()
    return Path(tmp.name), title, fmt


# ── Gemini VLM ────────────────────────────────────────────────────────────────

GEMINI_PROMPT = (
    "Convert this document page to well-structured markdown. "
    "Extract ALL text precisely.\n\n"
    "For administrative documents, clearly identify and highlight:\n"
    "- Personal identifiers (passport numbers, ID numbers, client numbers, "
    "social security numbers)\n"
    "- Credentials (login, passwords, access codes)\n"
    "- Dates (issue dates, expiry dates, deadlines, birth dates)\n"
    "- Locations (addresses, cities, countries)\n"
    "- People and their roles (signatories, mandated persons, "
    "representatives, beneficiaries)\n"
    "- Financial amounts (costs, revenues, taxes, fees, totals "
    "with currency)\n"
    "- Reference numbers (invoice numbers, contract numbers, case numbers)\n\n"
    "Format these as bold or in a clearly labeled section. "
    "Do not miss any text. Output only the bare markdown."
)


def _require_gemini_key(settings: Settings) -> str:
    if not settings.gemini_api_key:
        console.print("[red]GEMINI_API_KEY env var is required for --gemini and image conversion[/red]")
        raise typer.Exit(1)
    return settings.gemini_api_key


def build_gemini_vlm_options(settings: Settings) -> ApiVlmOptions:
    """Build VLM options for Gemini API."""
    return ApiVlmOptions(
        url=settings.gemini_url,
        params={"model": settings.gemini_model, "max_tokens": settings.gemini_max_tokens},
        headers={"Authorization": f"Bearer {_require_gemini_key(settings)}"},
        prompt=GEMINI_PROMPT,
        scale=2.0,
        timeout=settings.gemini_timeout,
        response_format=ResponseFormat.MARKDOWN,
        temperature=0.0,
    )


# ── PDF local extraction helpers ──────────────────────────────────────────────


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


def build_page_annotated_markdown(  # noqa: PLR0912
    doc: object,
    figure_map: dict[str, str],
    title: str = "",
    pdf_meta: dict[str, str] | None = None,
) -> str:
    """Build markdown with page number annotations."""
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
            description = get_vlm_description(item)
            fig_path = figure_map.get(item.self_ref, "")

            lines.append(f"[Figure: {caption}]" if caption else "[Figure]")
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


def build_images_catalog(doc: object, figure_map: dict[str, str]) -> str:
    """Build an images.md catalog (similar to pdf-extractor output)."""
    lines: list[str] = ["# Image Catalog\n"]

    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        if not isinstance(item, PictureItem):
            continue
        fig_path = figure_map.get(item.self_ref, "")
        if not fig_path:
            continue

        description = get_vlm_description(item)
        caption = item.caption_text(doc)
        classification = get_picture_classification(item)

        lines.append(f"## {Path(fig_path).name}\n")
        lines.append(f"- **Path:** {fig_path}")
        if classification:
            lines.append(f"- **Type:** {classification}")
        if caption:
            lines.append(f"- **Caption:** {caption}")
        if description:
            lines.append(f"- **Description:** {description}")
        lines.append(f"\n![{Path(fig_path).stem}]({fig_path})\n")

    return "\n".join(lines)


# ── Conversion functions ──────────────────────────────────────────────────────


def convert_pdf_local(
    pdf_path: Path,
    output_dir: Path,
    *,
    do_ocr: bool,
    vlm: bool,
    vlm_preset: str,
    figures: bool,
    all_formats: bool,
) -> None:
    """Full PDF extraction with local models."""
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline_options = PdfPipelineOptions(
        do_ocr=do_ocr,
        do_table_structure=True,
        generate_page_images=figures,
        generate_picture_images=figures,
        do_picture_description=vlm and figures,
        do_picture_classification=figures,
    )

    if vlm:
        pipeline_options.picture_description_options = PictureDescriptionVlmEngineOptions.from_preset(vlm_preset)
        logger.info("VLM picture description enabled (preset: %s)", vlm_preset)

    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)})

    with trace_span("docling.convert_pdf_local", file=pdf_path.name):
        logger.info("Converting %s (local pipeline)", pdf_path.name)
        result = converter.convert(str(pdf_path))
        logger.info("Status: %s", result.status)

    doc = result.document

    # Extract figures (skip entirely when --no-figures)
    figure_map: dict[str, str] = {}
    fig_count = 0
    if figures:
        fig_dir = output_dir / "figures"
        fig_dir.mkdir(exist_ok=True)
        for item, _ in doc.iterate_items():
            if isinstance(item, PictureItem):
                img = item.get_image(doc)
                if img:
                    filename = f"figure_{fig_count}.png"
                    img.save(fig_dir / filename)
                    figure_map[item.self_ref] = f"figures/{filename}"
                    fig_count += 1

    title = get_document_title(doc, str(pdf_path))
    pdf_meta = get_pdf_metadata(str(pdf_path))

    page_md = build_page_annotated_markdown(doc, figure_map, title, pdf_meta)
    (output_dir / "document.md").write_text(page_md)

    if fig_count > 0:
        catalog = build_images_catalog(doc, figure_map)
        (output_dir / "images.md").write_text(catalog)

    if all_formats:
        (output_dir / "output.md").write_text(doc.export_to_markdown())
        (output_dir / "output_embedded.md").write_text(doc.export_to_markdown(image_mode=ImageRefMode.EMBEDDED))
        (output_dir / "output.json").write_text(json.dumps(doc.export_to_dict(), indent=2, default=str))
        (output_dir / "output.txt").write_text(doc.export_to_text())
        (output_dir / "output.html").write_text(doc.export_to_html())

    if title:
        logger.info("Title: %s", title)
    console.print(f"[green]Output:[/green] {output_dir}/")
    console.print("  document.md  (page-annotated markdown)")
    if fig_count > 0:
        console.print("  images.md    (image catalog)")
        console.print(f"  figures/     ({fig_count} figure(s))")
    if all_formats:
        console.print("  output.*     (md, html, json, txt, embedded)")


def convert_with_gemini(doc_path: Path, fmt: InputFormat, settings: Settings) -> str:
    """Convert PDF or image via Gemini VLM pipeline."""
    vlm_opts = build_gemini_vlm_options(settings)
    pipeline_options = VlmPipelineOptions(
        enable_remote_services=True,
        vlm_options=vlm_opts,
    )

    format_options: dict[InputFormat, FormatOption] = {}
    if fmt == InputFormat.PDF:
        format_options[InputFormat.PDF] = PdfFormatOption(
            pipeline_cls=VlmPipeline,
            pipeline_options=pipeline_options,
        )
    else:
        format_options[InputFormat.IMAGE] = ImageFormatOption(
            pipeline_cls=VlmPipeline,
            pipeline_options=pipeline_options,
        )

    converter = DocumentConverter(
        allowed_formats=[fmt],
        format_options=format_options,
    )

    with trace_span("docling.convert_gemini", file=doc_path.name, format=fmt.value):
        logger.info("Converting %s (Gemini VLM)", doc_path.name)
        result = converter.convert(str(doc_path))
        logger.info("Status: %s", result.status)

    return result.document.export_to_markdown()  # type: ignore[no-any-return]


def convert_native(doc_path: Path, fmt: InputFormat) -> str:
    """Convert DOCX/XLSX with native parsers."""
    if fmt == InputFormat.DOCX:
        converter = DocumentConverter(
            allowed_formats=[InputFormat.DOCX],
            format_options={InputFormat.DOCX: WordFormatOption()},
        )
    elif fmt == InputFormat.XLSX:
        converter = DocumentConverter(
            allowed_formats=[InputFormat.XLSX],
            format_options={InputFormat.XLSX: ExcelFormatOption()},
        )
    else:
        console.print(f"[red]Unsupported format: {fmt}[/red]")
        raise typer.Exit(1)

    with trace_span("docling.convert_native", file=doc_path.name, format=fmt.value):
        logger.info("Converting %s (%s)", doc_path.name, fmt.value)
        result = converter.convert(str(doc_path))
        logger.info("Status: %s", result.status)

    return result.document.export_to_markdown()  # type: ignore[no-any-return]


# ── Output helpers ────────────────────────────────────────────────────────────


def write_markdown_output(md: str, output: str | None, auto_output: bool, name: str) -> None:
    """Write markdown to file or stdout."""
    if output:
        out = Path(output)
        out.write_text(md)
        console.print(f"[green]Written to {out}[/green]")
    elif auto_output:
        safe_name = re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")
        out = Path(f"{safe_name}.md")
        out.write_text(md)
        console.print(f"[green]Written to {out}[/green]")
    else:
        typer.echo(md)


# ── CLI command ───────────────────────────────────────────────────────────────


@app.command()
def convert(
    document: str = typer.Argument(
        help="File path (PDF, image, DOCX, XLSX) or Google Docs/Sheets URL",
    ),
    output: str | None = typer.Option(
        None,
        "-o",
        "--output",
        help="Output directory (PDF local) or file path (other formats)",
    ),
    auto_output: bool = typer.Option(
        False,
        "-O",
        "--auto-output",
        help="Auto-name: <stem>_docling/ for PDF local, <stem>.md for others",
    ),
    gemini: bool = typer.Option(
        False,
        "--gemini",
        help="Use Gemini API instead of local models (PDF and images)",
    ),
    vlm_preset: VlmPreset = typer.Option(
        VlmPreset.smolvlm,
        "--vlm-preset",
        help="Local VLM preset for picture descriptions",
    ),
    no_ocr: bool = typer.Option(
        False,
        "--no-ocr",
        help="Disable OCR (local PDF only)",
    ),
    no_vlm: bool = typer.Option(
        False,
        "--no-vlm",
        help="Disable VLM picture descriptions (local PDF only)",
    ),
    no_figures: bool = typer.Option(
        False,
        "--no-figures",
        help="Skip figure extraction entirely (text + tables only, faster)",
    ),
    all_formats: bool = typer.Option(
        False,
        "--all",
        help="Generate all export formats (local PDF: md, html, json, txt)",
    ),
    verbose: int = typer.Option(
        0,
        "-v",
        "--verbose",
        count=True,
        help="Increase verbosity (use -vv for debug)",
    ),
    quiet: bool = typer.Option(
        False,
        "-q",
        "--quiet",
        help="Only show warnings and errors",
    ),
) -> None:
    """Convert documents to markdown using Docling."""
    setup_logging(verbose=verbose, quiet=quiet)
    configure_tracing("doc-convert")
    settings = Settings()

    tmp_file: Path | None = None
    try:
        if is_google_url(document):
            tmp_file, title, fmt = download_google_doc(document, settings)
            doc_path = tmp_file
            doc_name = title
        else:
            doc_path = Path(document)
            if not doc_path.exists():
                console.print(f"[red]{doc_path} not found[/red]")
                raise typer.Exit(1)
            fmt = detect_format(doc_path)
            doc_name = doc_path.stem

        if fmt == InputFormat.PDF and not gemini:
            out_dir = Path(output) if output else doc_path.parent / f"{doc_path.stem}_docling"
            convert_pdf_local(
                pdf_path=doc_path,
                output_dir=out_dir,
                do_ocr=not no_ocr,
                vlm=not no_vlm,
                vlm_preset=vlm_preset.value,
                figures=not no_figures,
                all_formats=all_formats,
            )
        elif (fmt in (InputFormat.PDF, InputFormat.IMAGE) and gemini) or fmt == InputFormat.IMAGE:
            md = convert_with_gemini(doc_path, fmt, settings)
            write_markdown_output(md, output, auto_output, doc_name)
        else:
            md = convert_native(doc_path, fmt)
            write_markdown_output(md, output, auto_output, doc_name)
    finally:
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()
