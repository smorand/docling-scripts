"""Note creation from conversion output.

Uses Claude Sonnet 4.6 via OpenRouter to generate structured note metadata,
then stores the note via the Notes REST API (Google OAuth2 auth).

## Flow
1. Read analysis.md (or document.md) from the _docling/ output
2. GET /api/v1/folders to discover available folders
3. Send content + folders to Sonnet 4.6 → {path, title, tags, type, content}
4. POST /api/v1/notes to store the note
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx

from config import Settings  # noqa: TC001
from doc_convert.providers import PROVIDER_URLS
from logging_config import console
from tracing import trace_span

logger = logging.getLogger(__name__)

NOTE_MODEL = "anthropic/claude-sonnet-4.6"

NOTE_SYSTEM_PROMPT = """\
You are a note organizer. Given a document analysis and the list of available \
folders, generate a structured note for storage in a personal knowledge system.

## Available folders
{folders}

## Tag conventions
- All tags are lowercase-hyphenated
- MANDATORY: include date tags when dates are known (year: 2026, month: 2026-04)
- Include document-type tag: meeting-minutes, meeting-analysis, article-summary, \
project-proposal, status-report, architecture-doc, etc.
- Include context tag: professional, personal, tech, travel, finance
- Include entity tags: company names (schneider-electric, ibm), person names (firstname-lastname)

## Note types (choose one)
note, todo, identity, admin, finance, pro, travel, health, order, culture, tech

## Output format
Respond with ONLY valid JSON (no markdown fences, no explanation):
{{
  "path": "folder/note-name-with-key-identifiers",
  "title": "Human readable title",
  "tags": ["tag1", "tag2", "2026", "2026-04"],
  "type": "pro",
  "content": "Concise markdown summary. NOT a full copy of the document. \
Capture the essential information, decisions, and action items."
}}
"""


def _get_notes_token(settings: Settings) -> str:
    """Get Google OAuth2 access token for Notes API.

    Reuses GOOGLE_CREDENTIALS (same as Google Docs/Sheets).
    The Notes API validates tokens via Google userinfo endpoint.
    """
    creds_path = settings.google_credentials
    if not creds_path:
        msg = "GOOGLE_CREDENTIALS env var is required for --note"
        console.print(f"[red]{msg}[/red]")
        raise SystemExit(1)

    creds_file = Path(os.path.expandvars(creds_path)).expanduser()
    if not creds_file.exists():
        console.print(f"[red]Credentials file not found: {creds_file}[/red]")
        raise SystemExit(1)

    from google.oauth2 import credentials as user_credentials  # noqa: PLC0415
    from google.oauth2 import service_account  # noqa: PLC0415

    creds_data = json.loads(creds_file.read_text())
    scopes = [
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]

    cred_type = creds_data.get("type", "")
    if cred_type == "service_account":
        creds = service_account.Credentials.from_service_account_file(str(creds_file), scopes=scopes)
    elif cred_type == "authorized_user":
        creds = user_credentials.Credentials.from_authorized_user_file(str(creds_file), scopes=scopes)
    else:
        console.print(f"[red]Unsupported credential type '{cred_type}'[/red]")
        raise SystemExit(1)

    from google.auth.transport.requests import Request as AuthRequest  # noqa: PLC0415

    if not creds.valid:
        creds.refresh(AuthRequest())
    return creds.token


def _list_folders(api_base: str, token: str) -> list[dict]:
    """GET /api/v1/folders to discover available folders."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            f"{api_base}/api/v1/folders",
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
    return resp.json().get("data", resp.json() if isinstance(resp.json(), list) else [])


def _store_note(api_base: str, token: str, note_data: dict) -> dict:
    """POST /api/v1/notes to create the note."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{api_base}/api/v1/notes",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "path": note_data["path"],
                "title": note_data["title"],
                "content": note_data["content"],
                "type": note_data.get("type", "note"),
                "tags": note_data.get("tags", []),
            },
        )
        resp.raise_for_status()
    return resp.json()


def _generate_note_metadata(
    document_content: str,
    folders: list[dict],
    lang: str | None,
    settings: Settings,
) -> dict:
    """Call Sonnet 4.6 to generate {path, title, tags, type, content}."""
    folders_text = "\n".join(f"- {f.get('path', f.get('name', ''))}: {f.get('description', '')}" for f in folders)
    system = NOTE_SYSTEM_PROMPT.format(folders=folders_text)
    if lang:
        system = f"IMPORTANT: Write the note content in {lang}.\n\n{system}"

    if not settings.openrouter_api_key:
        console.print("[red]OPENROUTER_API_KEY is required for --note (uses Claude Sonnet 4.6)[/red]")
        raise SystemExit(1)

    with trace_span("note.generate", model=NOTE_MODEL):
        logger.info("Generating note with %s", NOTE_MODEL)
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                PROVIDER_URLS["openrouter"],
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": NOTE_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"Generate a note from this document:\n\n{document_content}"},
                    ],
                },
            )
            resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"]
    # Strip markdown fences if present
    if raw.strip().startswith("```"):
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)


def create_note_from_conversion(
    output_dir: Path,
    settings: Settings,
    *,
    lang: str | None = None,
) -> bool:
    """Main entry point: read conversion output, generate note, store it.

    Reads analysis.md if present, otherwise document.md.
    Returns True if note was stored successfully.
    """
    try:
        # Read content (prefer analysis, fallback to document)
        analysis = output_dir / "analysis.md"
        document = output_dir / "document.md"
        if analysis.exists():
            content = analysis.read_text()
        elif document.exists():
            content = document.read_text()
        else:
            logger.warning("No document.md or analysis.md found in %s", output_dir)
            return False

        # Get Google OAuth2 token
        token = _get_notes_token(settings)

        # List folders
        api_base = settings.notes_api_url
        with trace_span("note.list_folders"):
            folders = _list_folders(api_base, token)
        logger.info("Note: found %d folder(s) in Notes system", len(folders))

        # Generate note metadata via Sonnet 4.6
        note_data = _generate_note_metadata(content, folders, lang, settings)
        logger.info("Note: generated path=%s, title=%s", note_data.get("path"), note_data.get("title"))

        # Store the note
        with trace_span("note.store", path=note_data.get("path", "")):
            _store_note(api_base, token, note_data)
        logger.info("Note stored: %s", note_data["path"])
        console.print(f"  [green]Note stored:[/green] {note_data['path']}")

        # Also save the draft locally for reference
        draft_path = output_dir / "note.json"
        draft_path.write_text(json.dumps(note_data, indent=2, ensure_ascii=False))

        return True
    except Exception:
        logger.warning("Failed to create note from %s", output_dir, exc_info=True)
        return False
