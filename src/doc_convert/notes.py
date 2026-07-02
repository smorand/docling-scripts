"""Note creation from conversion output.

Uses Gemini Flash via Google API to generate structured note metadata
(path, title, tags, type), then stores the note via the Notes REST API
(Google OAuth2 auth) with the full analyze.md as content.

Attachments (source file, companion .md, referenced files, document.md)
are uploaded via signed URLs after note creation.

## Flow
1. Read analyze.md (or document.md) from the _docling/ output
2. Send content + folders + routing rules to Gemini Flash -> {path, title, tags, type}
3. POST /api/v1/notes to store the note (content = raw analyze.md)
4. Upload attachments via prepare-upload -> signed URL PUT
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, cast

import httpx

from config import Settings  # noqa: TC001
from doc_convert.providers import get_provider_url
from logging_config import console
from tracing import trace_span

logger = logging.getLogger(__name__)

# IBM ICA (which fronts Gemini) so note extraction needs no separate
# GOOGLE_API_KEY; gemini-3.5-flash is the current flash tier on ICA.
NOTE_MODEL = "ibm/gemini-3.5-flash"

NOTE_SYSTEM_PROMPT = """\
You are a note organizer. Given a document analysis and the list of available \
folders, generate structured metadata for storage in a personal knowledge system.

## Available folders
{folders}

{routing_rules}

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
  "type": "pro"
}}
"""

DEFAULT_ROUTING_RULES = """\
## Routing rules
- Meeting minutes and one-on-one notes go to professional/
- Technology articles, tools, and architecture docs go to tech/
- Travel bookings and tickets go to travel/
- Invoices and receipts go to finance/
- Identity documents (passport, ID cards) go to identity/
- WiFi passwords and credentials go to credentials/
- Purchases and deliveries go to orders/
- Medical records go to health/
- Personal notes and ideas go to personal/"""


OAUTH2_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"  # nosec B105 - public OAuth2 endpoint URL, not a secret
OAUTH2_REDIRECT_URI = "http://localhost:3000/oauth2callback"
OAUTH2_SCOPES = "openid email profile"
TOKEN_CACHE_FILE = Path.home() / ".cache" / "doc-convert" / "notes_token.json"

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB


def _get_notes_token(settings: Settings) -> str:
    """Get Google OAuth2 access token for Notes API.

    Uses OAuth2 authorization code flow with GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.
    Tokens are cached in ~/.cache/doc-convert/notes_token.json and refreshed automatically.
    On first use, opens a browser for Google login with callback on localhost:3000/oauth2callback.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        console.print("[red]GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars are required for --note[/red]")
        raise SystemExit(1)

    # Try cached token first
    if TOKEN_CACHE_FILE.exists():
        cached = json.loads(TOKEN_CACHE_FILE.read_text())
        access_token: str = cached.get("access_token", "")
        refresh_token: str = cached.get("refresh_token", "")

        if access_token:
            # Validate token
            resp = httpx.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.is_success:
                return access_token

        # Try refresh
        if refresh_token:
            refreshed = _refresh_token(settings, refresh_token)
            if refreshed:
                return refreshed

    # Full OAuth2 flow: open browser, wait for callback
    return _run_oauth2_flow(settings)


def _refresh_token(settings: Settings, refresh_token: str) -> str | None:
    """Refresh an expired access token. Returns new access token or None."""
    try:
        resp = httpx.post(
            OAUTH2_TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if resp.is_success:
            data = resp.json()
            # Update cache (keep refresh_token, update access_token)
            TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            cached = json.loads(TOKEN_CACHE_FILE.read_text()) if TOKEN_CACHE_FILE.exists() else {}
            cached["access_token"] = data["access_token"]
            TOKEN_CACHE_FILE.write_text(json.dumps(cached))
            logger.info("Notes: refreshed OAuth2 token")
            return cast("str", data["access_token"])
    except Exception:
        logger.debug("Token refresh failed")
    return None


def _run_oauth2_flow(settings: Settings) -> str:
    """Run full OAuth2 authorization code flow with local callback server."""
    import secrets  # noqa: PLC0415
    import threading  # noqa: PLC0415
    import webbrowser  # noqa: PLC0415
    from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: PLC0415
    from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

    state = secrets.token_urlsafe(32)
    auth_code: list[str] = []
    server_error: list[str] = []

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/oauth2callback":
                self.send_response(404)
                self.end_headers()
                return

            params = parse_qs(parsed.query)
            if params.get("state", [None])[0] != state:
                server_error.append("State mismatch")
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"State mismatch. Please try again.")
                return

            if "code" in params:
                auth_code.append(params["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>Authentication successful!</h2><p>You can close this tab.</p></body></html>"
                )
            else:
                error = params.get("error", ["unknown"])[0]
                server_error.append(error)
                self.send_response(400)
                self.end_headers()
                self.wfile.write(f"Error: {error}".encode())

        def log_message(self, format: str, *args: object) -> None:
            pass  # Suppress HTTP server logs

    # Start callback server (try port 3000, fallback to 3001)
    port = 3000
    for p in (3000, 3001, 3002):
        try:
            server = HTTPServer(("localhost", p), CallbackHandler)
            server.socket.setsockopt(__import__("socket").SOL_SOCKET, __import__("socket").SO_REUSEADDR, 1)
            port = p
            break
        except OSError:
            if p == 3002:  # noqa: PLR2004
                console.print("[red]Ports 3000-3002 are all in use. Free one and retry.[/red]")
                raise SystemExit(1) from None

    redirect_uri = f"http://localhost:{port}/oauth2callback"
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    # Open browser for authorization
    auth_url = (
        f"{OAUTH2_AUTH_URL}?"
        f"client_id={settings.google_client_id}&"
        f"redirect_uri={redirect_uri}&"
        f"response_type=code&"
        f"scope={OAUTH2_SCOPES}&"
        f"state={state}&"
        f"access_type=offline&"
        f"prompt=consent"
    )

    console.print("[bold]Opening browser for Google authentication...[/bold]")
    webbrowser.open(auth_url)

    # Wait for callback
    thread.join(timeout=120)
    server.server_close()

    if server_error:
        console.print(f"[red]OAuth2 error: {server_error[0]}[/red]")
        raise SystemExit(1)
    if not auth_code:
        console.print("[red]OAuth2 timeout: no callback received within 2 minutes[/red]")
        raise SystemExit(1)

    # Exchange code for tokens
    resp = httpx.post(
        OAUTH2_TOKEN_URL,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code": auth_code[0],
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
    )
    resp.raise_for_status()
    data = resp.json()

    # Cache tokens
    TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_FILE.write_text(
        json.dumps(
            {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", ""),
            }
        )
    )
    logger.info("Notes: OAuth2 tokens cached to %s", TOKEN_CACHE_FILE)

    return cast("str", data["access_token"])


KNOWN_FOLDERS = """- professional/: Work meetings, projects, partners, proposals
- tech/: Technology notes, articles, tools, architecture
- travel/: Flights, hotels, bookings, itineraries
- finance/: Invoices, bank accounts, receipts
- administrative/: General admin, official documents, emails
- identity/: Identity documents (passport, ID cards)
- credentials/: WiFi passwords, logins
- orders/: Purchases, deliveries
- health/: Medical records, prescriptions
- personal/: Personal notes, ideas"""


def _list_folders(api_base: str, token: str) -> str:
    """Fetch real folder list from Notes API.

    Returns formatted string like KNOWN_FOLDERS. Falls back to KNOWN_FOLDERS on error.
    """
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                f"{api_base}/api/v1/folders",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body)
        folders = data.get("folders", data if isinstance(data, list) else [])
        if not folders:
            logger.warning("Notes API returned empty folder list, using fallback")
            return KNOWN_FOLDERS
        lines = []
        for f in folders:
            path = f.get("path", "")
            desc = f.get("description", "")
            if path:
                lines.append(f"- {path}/: {desc}" if desc else f"- {path}/")
        return "\n".join(lines)
    except Exception:
        logger.warning("Failed to list folders from Notes API, using fallback", exc_info=True)
        return KNOWN_FOLDERS


def _search_similar(api_base: str, token: str, query: str, *, mode: str = "vector") -> list[dict[str, Any]]:
    """Search for existing notes similar to the given content.

    Uses POST /api/v1/search with full content for accurate vector similarity.
    Returns list of matching notes with path, title, and score.
    """
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{api_base}/api/v1/search",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"q": query, "mode": mode, "page_size": 5},
        )
        resp.raise_for_status()
    body = resp.json()
    results = (
        body.get("data", body).get("results", []) if isinstance(body.get("data"), dict) else body.get("results", [])
    )
    return [{"path": r.get("path", ""), "title": r.get("title", ""), "score": r.get("score", 0)} for r in results]


def _sanitize_path(path: str) -> str:
    """Sanitize note path: only allow a-z, 0-9, _, -, /."""
    # Replace dots and other invalid chars with hyphens
    sanitized = re.sub(r"[^a-zA-Z0-9_\-/]", "-", path)
    # Collapse multiple hyphens
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    # Strip trailing hyphens from each segment
    sanitized = "/".join(seg.strip("-") for seg in sanitized.split("/"))
    return sanitized.lower()


def _store_note(api_base: str, token: str, note_data: dict[str, Any]) -> dict[str, Any]:
    """POST /api/v1/notes to create the note."""
    note_data["path"] = _sanitize_path(note_data["path"])
    with httpx.Client(timeout=300.0) as client:
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
        if not resp.is_success:
            logger.error("Notes API error %d: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
    return cast("dict[str, Any]", resp.json())


def _generate_note_metadata(
    document_content: str,
    lang: str | None,
    settings: Settings,
    *,
    api_base: str,
    token: str,
) -> dict[str, Any]:
    """Call Gemini Flash to generate {path, title, tags, type}."""
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    note_model = get_prompt("notes", "model", NOTE_MODEL)
    note_prompt = get_prompt("notes", "system_prompt", NOTE_SYSTEM_PROMPT)
    folders = _list_folders(api_base, token)
    routing_rules = get_prompt("notes", "routing_rules", DEFAULT_ROUTING_RULES)
    system = note_prompt.format(folders=folders, routing_rules=routing_rules)
    if lang:
        system = f"IMPORTANT: Generate the title in {lang}.\n\n{system}"

    # Resolve provider/model/key from the note_model spec (e.g. "google/gemini-2.5-flash")
    from doc_convert.providers import parse_external_llm, require_api_key  # noqa: PLC0415

    provider, model = parse_external_llm(note_model)
    api_key = require_api_key(provider, settings)

    with trace_span("note.generate", model=note_model):
        logger.info("Generating note metadata with %s", note_model)
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                get_provider_url(provider, settings),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": f"Generate metadata for this document:\n\n{document_content}"},
                    ],
                },
            )
            resp.raise_for_status()

    raw = resp.json()["choices"][0]["message"]["content"]
    # Strip markdown fences if present
    if raw.strip().startswith("```"):
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return cast("dict[str, Any]", json.loads(raw))


def _build_deterministic_path(folder: str, source_stem: str) -> str:
    """Build a deterministic note path from the folder and source filename.

    The path is always the same for the same source file, making
    duplicate detection reliable via exact path match.
    """
    slug = _sanitize_path(source_stem)
    return f"{folder.strip('/')}/{slug}"


# ── Attachment upload ────────────────────────────────────────────────────


def _sanitize_attachment_name(filename: str) -> str:
    """Sanitize filename for attachment: alphanumeric, hyphens, underscores, one dot before extension."""
    stem, _, ext = filename.rpartition(".")
    if not stem:
        stem = ext
        ext = ""
    # Clean stem: replace invalid chars with hyphens
    stem = re.sub(r"[^a-zA-Z0-9_\-]", "-", stem)
    stem = re.sub(r"-{2,}", "-", stem).strip("-")
    if ext:
        ext = re.sub(r"[^a-zA-Z0-9]", "", ext)
        return f"{stem}.{ext}"
    return stem


def _guess_mime_type(path: Path) -> str:
    """Return MIME type for a file path."""
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def _upload_attachment(
    api_base: str,
    token: str,
    note_path: str,
    file_path: Path,
    *,
    attachment_name: str | None = None,
) -> bool:
    """Upload a single attachment to an existing note.

    Two-step: POST prepare-upload -> PUT signed URL.
    Returns True on success, False on failure (logged as warning).
    """
    name = _sanitize_attachment_name(attachment_name or file_path.name)
    mime_type = _guess_mime_type(file_path)

    try:
        with httpx.Client(timeout=60.0) as client:
            # Step 1: get signed URL
            prep_resp = client.post(
                f"{api_base}/api/v1/notes/{note_path}/attachments/prepare-upload",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"name": name, "mime_type": mime_type},
            )
            prep_resp.raise_for_status()
            signed_url = prep_resp.json().get("signed_url") or prep_resp.json().get("url")
            if not signed_url:
                logger.warning("Attachment: no signed_url in prepare-upload response for %s", name)
                return False

        # Step 2: PUT file bytes to signed URL
        file_bytes = file_path.read_bytes()
        with httpx.Client(timeout=300.0) as client:
            put_resp = client.put(
                signed_url,
                content=file_bytes,
                headers={"Content-Type": mime_type},
            )
            put_resp.raise_for_status()

        logger.info("Attachment uploaded: %s (%s, %.1f KB)", name, mime_type, len(file_bytes) / 1024)
        return True
    except Exception:
        logger.warning("Failed to upload attachment %s", name, exc_info=True)
        return False


def _collect_attachments(
    source_path: Path,
    output_dir: Path,
    companion_path: Path | None,
    companion_ref_paths: list[Path],
) -> list[tuple[Path, str | None]]:
    """Collect all files to attach, applying size rules.

    Returns list of (file_path, optional_display_name) tuples.
    """
    attachments: list[tuple[Path, str | None]] = []

    # 1. Source file: attach if < 10MB
    if source_path.exists():
        size = source_path.stat().st_size
        if size < MAX_ATTACHMENT_SIZE:
            attachments.append((source_path, None))
        else:
            logger.info("Attachment: skipping source %s (%.1f MB > 10 MB)", source_path.name, size / 1024 / 1024)

    # 2. Companion .md: always attach
    if companion_path and companion_path.exists():
        attachments.append((companion_path, None))

    # 3. Files referenced in companion: attach if < 10MB each
    for ref_path in companion_ref_paths:
        if not ref_path.exists():
            continue
        size = ref_path.stat().st_size
        if size < MAX_ATTACHMENT_SIZE:
            attachments.append((ref_path, None))
        else:
            logger.info("Attachment: skipping ref %s (%.1f MB > 10 MB)", ref_path.name, size / 1024 / 1024)

    # 4. document.md from _docling/ output: always attach
    doc_md = output_dir / "document.md"
    if doc_md.exists():
        attachments.append((doc_md, "document.md"))

    return attachments


def _upload_attachments(
    api_base: str,
    token: str,
    note_path: str,
    source_path: Path,
    output_dir: Path,
    *,
    companion_name_override: str | None = None,
) -> None:
    """Collect and upload all attachments for a note."""
    from doc_convert.companion import detect_companion, resolve_reference_paths  # noqa: PLC0415

    companion_path = detect_companion(source_path, name_override=companion_name_override)
    ref_paths = resolve_reference_paths(companion_path) if companion_path else []
    attachments = _collect_attachments(source_path, output_dir, companion_path, ref_paths)

    if not attachments:
        return

    uploaded = 0
    for file_path, display_name in attachments:
        name = display_name or file_path.name
        if _upload_attachment(api_base, token, note_path, file_path, attachment_name=name):
            uploaded += 1
            console.print(f"  [dim]attachment: {name}[/dim]")
        else:
            console.print(f"  [yellow]attachment failed: {name}[/yellow]")

    if uploaded:
        logger.info("Note: %d attachment(s) uploaded", uploaded)


# ── Main entry point ──────────────────────────────────────���──────────────


def create_note_from_conversion(
    output_dir: Path,
    settings: Settings,
    *,
    source_path: Path | None = None,
    companion_name_override: str | None = None,
    lang: str | None = None,
    similarity_threshold: float = 0.85,
    note_force: bool = False,
) -> bool:
    """Main entry point: read conversion output, generate metadata, store note, upload attachments.

    The note content is the raw analyze.md (or document.md fallback).
    Gemini Flash generates only the metadata: path, title, tags, type.
    The note path is built deterministically from the source filename.
    Returns True if note was stored successfully.
    """
    try:
        # Read content (prefer analysis, fallback to document)
        analysis = output_dir / "analyze.md"
        document = output_dir / "document.md"
        if analysis.exists():
            content = analysis.read_text()
        elif document.exists():
            content = document.read_text()
        else:
            logger.warning("No document.md or analyze.md found in %s", output_dir)
            return False

        # Get Google OAuth2 token
        token = _get_notes_token(settings)
        api_base = settings.notes_api_url

        # Derive deterministic source stem from output dir name
        source_stem = output_dir.name.replace("_docling", "")

        # Generate note metadata via Gemini Flash (path, title, tags, type only)
        note_data = _generate_note_metadata(content, lang, settings, api_base=api_base, token=token)

        # Use analyze.md as the note content directly
        note_data["content"] = content

        # Build deterministic path: folder from LLM + slug from source filename
        folder = (
            note_data.get("path", "professional").rsplit("/", 1)[0]
            if "/" in note_data.get("path", "")
            else note_data.get("path", "professional")
        )
        note_data["path"] = _build_deterministic_path(folder, source_stem)
        logger.info("Note: path=%s, title=%s", note_data["path"], note_data.get("title"))

        # Check for duplicate: exact path match (deterministic = reliable)
        if not note_force:
            with httpx.Client(timeout=30.0) as client:
                check_resp = client.get(
                    f"{api_base}/api/v1/notes",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"path": note_data["path"]},
                )
            if check_resp.is_success:
                logger.warning("Note: already exists at path %s. Skipping.", note_data["path"])
                console.print(f"  [yellow]Note already exists:[/yellow] {note_data['path']}")
                console.print("  [dim]Use --note-force to force creation[/dim]")
                return False

        # Check for semantic duplicates via vector search (catches similar notes under different paths)
        if content and not note_force:
            with trace_span("note.search_similar"):
                similar = _search_similar(api_base, token, content, mode="vector")
            if similar:
                top = similar[0]
                score = top.get("score", 0)
                if score > similarity_threshold:
                    logger.warning(
                        "Note: similar note exists: %s (score: %.2f). Skipping.",
                        top.get("path"),
                        score,
                    )
                    console.print(
                        f"  [yellow]Similar note exists:[/yellow] {top.get('path')} "
                        f"(title: {top.get('title')}, score: {score:.2f})"
                    )
                    console.print("  [dim]Use --note-force to force creation[/dim]")
                    return False
                logger.info("Note: no strong duplicate found (top score: %.2f)", score)

        # Store the note
        with trace_span("note.store", path=note_data.get("path", "")):
            _store_note(api_base, token, note_data)
        logger.info("Note stored: %s", note_data["path"])
        console.print(f"  [green]Note stored:[/green] {note_data['path']}")

        # Upload attachments
        if source_path:
            with trace_span("note.attachments"):
                _upload_attachments(
                    api_base,
                    token,
                    note_data["path"],
                    source_path,
                    output_dir,
                    companion_name_override=companion_name_override,
                )

        # Save draft locally for reference + marker so the step is not re-run
        draft_path = output_dir / "note.json"
        draft_path.write_text(json.dumps(note_data, indent=2, ensure_ascii=False))
        (output_dir / "note_sent").touch()

        return True
    except Exception:
        logger.warning("Failed to create note from %s", output_dir, exc_info=True)
        return False
