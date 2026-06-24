"""Google Docs/Sheets download support."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast

import httpx
import typer

from config import Settings  # noqa: TC001
from logging_config import console
from tracing import trace_span

if TYPE_CHECKING:
    from docling.datamodel.base_models import InputFormat

logger = logging.getLogger(__name__)

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
        creds = service_account.Credentials.from_service_account_file(str(creds_file), scopes=DRIVE_SCOPES)  # type: ignore[no-untyped-call]
    elif cred_type == "authorized_user":
        creds = user_credentials.Credentials.from_authorized_user_file(str(creds_file), scopes=DRIVE_SCOPES)  # type: ignore[no-untyped-call]
    else:
        console.print(f"[red]Unsupported credential type '{cred_type}'[/red]")
        raise typer.Exit(1)

    from google.auth.transport.requests import Request as AuthRequest  # noqa: PLC0415

    if not creds.valid:
        creds.refresh(AuthRequest())
    return cast("str", creds.token)


def download_google_doc(url: str, settings: Settings) -> tuple[Path, str, InputFormat]:
    """Download a Google Doc/Sheet to a temp file."""
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

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
