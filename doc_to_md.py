#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "docling @ file:///Users/sebastien/projects/external/docling",
#     "requests",
#     "google-auth",
# ]
# ///
"""Convert documents (PDF, images, DOCX, Google Docs/Sheets) to structured markdown.

Uses Gemini 3 Flash Preview via Google AI Studio for vision-based formats
(PDF, images) and Docling's native parser for DOCX/XLSX.

Usage:
    python scripts/doc_to_md.py document.pdf              # output to stdout
    python scripts/doc_to_md.py document.pdf -o out.md     # output to file
    python scripts/doc_to_md.py document.pdf -O            # output to <stem>.md
    python scripts/doc_to_md.py document.docx              # native DOCX parser
    python scripts/doc_to_md.py scan.png -O                # image via Gemini VLM
    python scripts/doc_to_md.py "https://docs.google.com/document/d/DOC_ID/edit"
    python scripts/doc_to_md.py "https://docs.google.com/spreadsheets/d/SHEET_ID/edit"

Requires:
    GEMINI_API_KEY env var for PDF and image formats.
    GOOGLE_CREDENTIALS env var for Google Docs/Sheets (path to service account
    or authorized user JSON credentials file).
    pip install google-auth  (only needed for Google Docs/Sheets support)
"""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import requests

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import VlmPipelineOptions
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

# Extension to InputFormat mapping
EXT_TO_FORMAT = {
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

VLM_PROMPT = (
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
    "- Financial amounts (costs, revenues, taxes, fees, totals — "
    "with currency)\n"
    "- Reference numbers (invoice numbers, contract numbers, case numbers)\n\n"
    "Format these as bold or in a clearly labeled section. "
    "Do not miss any text. Output only the bare markdown."
)

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
)

# Google URL patterns
GOOGLE_DOC_RE = re.compile(
    r"https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)"
)
GOOGLE_SHEET_RE = re.compile(
    r"https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)"
)

# Google Drive export MIME types
MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
MIME_XLSX = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

DRIVE_EXPORT_URL = "https://www.googleapis.com/drive/v3/files/{file_id}/export"
DRIVE_FILE_URL = "https://www.googleapis.com/drive/v3/files/{file_id}"
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]


def _load_google_credentials():
    """Load Google credentials from GOOGLE_CREDENTIALS env var."""
    creds_path = os.environ.get("GOOGLE_CREDENTIALS")
    if not creds_path:
        print(
            "Error: GOOGLE_CREDENTIALS environment variable is required "
            "for Google Docs/Sheets.\n"
            "Set it to the path of your service account or authorized user "
            "JSON credentials file.",
            file=sys.stderr,
        )
        sys.exit(1)

    creds_path = os.path.expandvars(os.path.expanduser(creds_path))
    if not os.path.exists(creds_path):
        print(
            f"Error: credentials file not found: {creds_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from google.oauth2 import credentials as user_credentials
        from google.oauth2 import service_account
    except ImportError:
        print(
            "Error: google-auth package is required for Google Docs/Sheets.\n"
            "Install it with: pip install google-auth",
            file=sys.stderr,
        )
        sys.exit(1)

    with open(creds_path) as f:
        creds_data = json.load(f)

    cred_type = creds_data.get("type", "")

    if cred_type == "service_account":
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=DRIVE_SCOPES
        )
    elif cred_type == "authorized_user":
        creds = user_credentials.Credentials.from_authorized_user_file(
            creds_path, scopes=DRIVE_SCOPES
        )
    else:
        print(
            f"Error: unsupported credential type '{cred_type}' in {creds_path}.\n"
            "Expected 'service_account' or 'authorized_user'.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Refresh token if needed
    from google.auth.transport.requests import Request as AuthRequest

    if not creds.valid:
        creds.refresh(AuthRequest())

    return creds


def _get_google_doc_title(file_id: str, headers: dict[str, str]) -> str:
    """Fetch the document title from Google Drive API."""
    resp = requests.get(
        DRIVE_FILE_URL.format(file_id=file_id),
        headers=headers,
        params={"fields": "name"},
    )
    if resp.ok:
        return resp.json().get("name", file_id)
    return file_id


def download_google_doc(url: str) -> tuple[Path, str, InputFormat]:
    """Download a Google Doc/Sheet as DOCX/XLSX to a temp file.

    Returns (temp_file_path, document_title, input_format).
    """
    doc_match = GOOGLE_DOC_RE.search(url)
    sheet_match = GOOGLE_SHEET_RE.search(url)

    if doc_match:
        file_id = doc_match.group(1)
        mime_type = MIME_DOCX
        suffix = ".docx"
        fmt = InputFormat.DOCX
        kind = "Google Doc"
    elif sheet_match:
        file_id = sheet_match.group(1)
        mime_type = MIME_XLSX
        suffix = ".xlsx"
        fmt = InputFormat.XLSX
        kind = "Google Sheet"
    else:
        print(f"Error: not a recognized Google Docs/Sheets URL: {url}", file=sys.stderr)
        sys.exit(1)

    creds = _load_google_credentials()
    headers = {"Authorization": f"Bearer {creds.token}"}

    # Get document title
    title = _get_google_doc_title(file_id, headers)
    print(f"Downloading {kind}: {title}...", file=sys.stderr)

    # Export document
    resp = requests.get(
        DRIVE_EXPORT_URL.format(file_id=file_id),
        headers=headers,
        params={"mimeType": mime_type},
    )
    if not resp.ok:
        print(
            f"Error: failed to export {kind} (HTTP {resp.status_code}):\n"
            f"{resp.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Write to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(resp.content)
    tmp.close()

    return Path(tmp.name), title, fmt


def is_google_url(source: str) -> bool:
    """Check if the input is a Google Docs or Sheets URL."""
    return bool(GOOGLE_DOC_RE.search(source) or GOOGLE_SHEET_RE.search(source))


def detect_format(path: Path) -> InputFormat:
    ext = path.suffix.lower()
    fmt = EXT_TO_FORMAT.get(ext)
    if fmt is None:
        print(f"Error: unsupported file extension '{ext}'", file=sys.stderr)
        print(
            f"Supported: {', '.join(sorted(EXT_TO_FORMAT.keys()))}",
            file=sys.stderr,
        )
        sys.exit(1)
    return fmt


def build_vlm_options() -> ApiVlmOptions:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print(
            "Error: GEMINI_API_KEY environment variable is required "
            "for PDF and image conversion.",
            file=sys.stderr,
        )
        sys.exit(1)

    return ApiVlmOptions(
        url=GEMINI_URL,
        params={"model": "gemini-3-flash-preview", "max_tokens": 8192},
        headers={"Authorization": f"Bearer {api_key}"},
        prompt=VLM_PROMPT,
        scale=2.0,
        timeout=120.0,
        response_format=ResponseFormat.MARKDOWN,
        temperature=0.0,
    )


def build_converter(fmt: InputFormat) -> DocumentConverter:
    if fmt == InputFormat.DOCX:
        return DocumentConverter(
            allowed_formats=[InputFormat.DOCX],
            format_options={InputFormat.DOCX: WordFormatOption()},
        )

    if fmt == InputFormat.XLSX:
        return DocumentConverter(
            allowed_formats=[InputFormat.XLSX],
            format_options={InputFormat.XLSX: ExcelFormatOption()},
        )

    # PDF or IMAGE — use VLM pipeline with Gemini
    vlm_opts = build_vlm_options()
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

    return DocumentConverter(
        allowed_formats=[fmt],
        format_options=format_options,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert documents to markdown using Docling + Gemini VLM"
    )
    parser.add_argument(
        "document",
        help="Input document path (PDF, image, DOCX, XLSX) or Google Docs/Sheets URL",
    )
    parser.add_argument("-o", "--output", help="Write output to this file")
    parser.add_argument(
        "-O",
        "--auto-output",
        action="store_true",
        help="Write output to <input_stem>.md in current directory",
    )
    args = parser.parse_args()

    tmp_file: Path | None = None
    try:
        # Handle Google Docs/Sheets URLs
        if is_google_url(args.document):
            tmp_file, title, fmt = download_google_doc(args.document)
            doc_path = tmp_file
            doc_name = title
        else:
            doc_path = Path(args.document)
            if not doc_path.exists():
                print(f"Error: {doc_path} not found", file=sys.stderr)
                sys.exit(1)
            fmt = detect_format(doc_path)
            doc_name = doc_path.stem

        print(f"Converting {doc_name} ({fmt.value})...", file=sys.stderr)

        converter = build_converter(fmt)
        result = converter.convert(str(doc_path))
        print(f"Status: {result.status}", file=sys.stderr)

        md = result.document.export_to_markdown()

        if args.output:
            out = Path(args.output)
            out.write_text(md)
            print(f"Written to {out}", file=sys.stderr)
        elif args.auto_output:
            # For Google URLs, use the document title; for files, use the stem
            safe_name = re.sub(r"[^\w\s-]", "", doc_name).strip().replace(" ", "_")
            out = Path(f"{safe_name}.md")
            out.write_text(md)
            print(f"Written to {out}", file=sys.stderr)
        else:
            print(md)
    finally:
        # Clean up temp file from Google download
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()


if __name__ == "__main__":
    main()
