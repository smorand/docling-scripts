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


OAUTH2_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"
OAUTH2_REDIRECT_URI = "http://localhost:3000/oauth2callback"
OAUTH2_SCOPES = "openid email profile"
TOKEN_CACHE_FILE = Path.home() / ".cache" / "doc-convert" / "notes_token.json"


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
        access_token = cached.get("access_token", "")
        refresh_token = cached.get("refresh_token", "")

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
            return data["access_token"]
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

    return data["access_token"]


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


def _search_similar(api_base: str, token: str, query: str, *, mode: str = "vector") -> list[dict]:
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
    results = resp.json().get("results", [])
    return [{"path": r.get("path", ""), "title": r.get("title", ""), "score": r.get("score", 0)} for r in results]


def _sanitize_path(path: str) -> str:
    """Sanitize note path: only allow a-z, 0-9, _, -, /."""
    import re as _re  # noqa: PLC0415

    # Replace dots and other invalid chars with hyphens
    sanitized = _re.sub(r"[^a-zA-Z0-9_\-/]", "-", path)
    # Collapse multiple hyphens
    sanitized = _re.sub(r"-{2,}", "-", sanitized)
    # Strip trailing hyphens from each segment
    sanitized = "/".join(seg.strip("-") for seg in sanitized.split("/"))
    return sanitized.lower()


def _store_note(api_base: str, token: str, note_data: dict) -> dict:
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
    return resp.json()


def _generate_note_metadata(
    document_content: str,
    lang: str | None,
    settings: Settings,
) -> dict:
    """Call Sonnet 4.6 to generate {path, title, tags, type, content}."""
    system = NOTE_SYSTEM_PROMPT.format(folders=KNOWN_FOLDERS)
    if lang:
        system = f"IMPORTANT: Write the note content in {lang}.\n\n{system}"

    if not settings.openrouter_api_key:
        console.print("[red]OPENROUTER_API_KEY is required for --note (uses Claude Sonnet 4.6)[/red]")
        raise SystemExit(1)

    with trace_span("note.generate", model=NOTE_MODEL):
        logger.info("Generating note with %s", NOTE_MODEL)
        with httpx.Client(timeout=300.0) as client:
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
    Searches for duplicates by source filename before generating.
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
        api_base = settings.notes_api_url

        # Generate note metadata via Sonnet 4.6
        note_data = _generate_note_metadata(content, lang, settings)
        logger.info("Note: generated path=%s, title=%s", note_data.get("path"), note_data.get("title"))

        # Check for duplicate: exact path match (always reliable)
        note_path = note_data.get("path", "")
        if note_path:
            with httpx.Client(timeout=30.0) as client:
                check_resp = client.get(
                    f"{api_base}/api/v1/notes",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"path": note_path},
                )
            if check_resp.is_success:
                logger.warning("Note: already exists at path %s. Skipping.", note_path)
                console.print(f"  [yellow]Note already exists:[/yellow] {note_path}")
                console.print("  [dim]Use -f to force creation[/dim]")
                return False

        # Check for semantic duplicates via vector search (when index is available)
        note_content = note_data.get("content", "")
        with trace_span("note.search_similar"):
            similar = _search_similar(api_base, token, note_content, mode="vector")
        if similar:
            top = similar[0]
            score = top.get("score", 0)
            if score > 0.8:  # noqa: PLR2004
                logger.warning(
                    "Note: similar note already exists: %s (score: %.2f). Skipping.",
                    top.get("path"),
                    score,
                )
                console.print(
                    f"  [yellow]Similar note exists:[/yellow] {top.get('path')} "
                    f"(title: {top.get('title')}, score: {score:.2f})"
                )
                console.print("  [dim]Use -f to force creation[/dim]")
                return False
            logger.info("Note: no strong duplicate found (top score: %.2f)", score)

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
