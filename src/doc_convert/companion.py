"""Companion file detection, loading, and context extraction.

When converting a file (e.g. meeting.ogg), automatically detect if a companion
file (meeting.md) exists in the same directory. If found, load it, resolve any
referenced documents (by calling doc-convert's own converters), and extract
structured context using a lightweight LLM.

The extracted context is injected into the meeting parameter, enriching
transcription/analysis prompts for all document types.

## Usage flow

1. `detect_companion(source_path)` finds the companion .md
2. `build_companion_bundle()` loads companion + converts referenced files
3. `analyze_companion()` extracts structured context via LLM
4. Result cached as `companion_context.md` in the output directory
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import httpx

from config import Settings  # noqa: TC001
from logging_config import console
from tracing import trace_span

logger = logging.getLogger(__name__)

# File references extraction
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
BARE_FILENAME_RE = re.compile(
    r"\b([\w. -]+\.(?:pdf|docx|pptx|xlsx|txt|csv|md|json|yaml|yml))\b",
    re.IGNORECASE,
)

# Text file extensions (loaded as raw text, no conversion needed)
TEXT_EXTENSIONS = {".md", ".txt", ".csv", ".json", ".yaml", ".yml"}

# Document extensions (converted via doc-convert's own converters)
CONVERTIBLE_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}

# Max file size for referenced files (5 MB)
MAX_REF_FILE_SIZE = 5 * 1024 * 1024

# Pre-analysis prompt
COMPANION_SYSTEM_PROMPT = """\
You are a context extraction assistant. Given meeting preparation notes and \
related documents, extract structured context that will help an AI transcribe \
and analyze the corresponding audio/video recording or document.

Extract the following information if present:

## Attendees
List of expected participants with their roles/titles if mentioned.

## Agenda / Topics
Key topics, agenda items, or discussion points expected.

## Key Terms & Acronyms
Domain-specific terms, project names, acronyms, product names that appear.
This helps with accurate transcription.

## Referenced Documents Summary
For each referenced document provided, a 1-2 sentence summary of its relevance.

## Additional Context
Any other contextual information (dates, project names, client names, \
deadlines) that would help understand the content.

Output in clean markdown. Be concise but preserve all proper nouns, \
technical terms, and specific details. If a section has no relevant \
information, omit it entirely.
"""


def detect_companion(source_path: Path, *, name_override: str | None = None) -> Path | None:
    """Check if a companion .md file exists for the given source file.

    For input file foo.xyz, checks if foo.md exists in the same directory.
    For --start-audio, name_override provides the name to search in CWD.
    """
    candidate = Path.cwd() / f"{name_override}.md" if name_override else source_path.with_suffix(".md")

    if candidate.exists() and candidate.is_file():
        return candidate
    return None


def _load_text_file(path: Path) -> str | None:
    """Safely load a text file. Returns None if binary or too large."""
    if path.stat().st_size > MAX_REF_FILE_SIZE:
        logger.debug("Skipping reference %s: file too large (%d bytes)", path.name, path.stat().st_size)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _convert_document(ref_path: Path, output_dir: Path, settings: Settings) -> str | None:
    """Convert a document file using doc-convert's own converters. Returns the document.md content."""
    from doc_convert.base import ConvertOptions  # noqa: PLC0415
    from doc_convert.formats import detect_format  # noqa: PLC0415

    try:
        fmt = detect_format(ref_path)
    except SystemExit:
        return None

    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

    ref_output = output_dir / f"_ref_{ref_path.stem}"
    options = ConvertOptions(output_dir=ref_output, figures=False, vlm=False, settings=settings)

    try:
        if fmt == InputFormat.PDF:
            from doc_convert.converters.pdf import PdfConverter  # noqa: PLC0415

            PdfConverter(ref_path, options).convert()
        elif fmt == InputFormat.PPTX:
            from doc_convert.converters.pptx import PptxConverter  # noqa: PLC0415

            PptxConverter(ref_path, options).convert()
        elif fmt == InputFormat.DOCX:
            from doc_convert.converters.docx import DocxConverter  # noqa: PLC0415

            DocxConverter(ref_path, options).convert()
        elif fmt == InputFormat.XLSX:
            from doc_convert.converters.xlsx import XlsxConverter  # noqa: PLC0415

            XlsxConverter(ref_path, options).convert()
        else:
            return None
    except Exception:
        logger.warning("Failed to convert referenced document: %s", ref_path.name)
        return None

    doc_md = ref_output / "document.md"
    if doc_md.exists():
        return doc_md.read_text()
    return None


def resolve_references(companion_path: Path, content: str, output_dir: Path, settings: Settings) -> dict[str, str]:
    """Extract file references from companion .md and load/convert them.

    Resolves markdown links and bare filenames. For text files, loads raw content.
    For documents (PDF, DOCX, PPTX, XLSX), runs through doc-convert converters.

    Returns {filename: content} for successfully loaded files.
    """
    companion_dir = companion_path.parent
    references: dict[str, str] = {}
    candidates: list[Path] = []

    # Extract markdown link targets (skip URLs)
    for match in MARKDOWN_LINK_RE.finditer(content):
        target = match.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        candidate = companion_dir / target
        if candidate.is_file() and candidate.name not in references:
            candidates.append(candidate)

    # Extract bare filename mentions
    for match in BARE_FILENAME_RE.finditer(content):
        filename = match.group(1)
        candidate = companion_dir / filename
        if candidate.is_file() and candidate.name not in references and candidate not in candidates:
            candidates.append(candidate)

    if not candidates:
        return references

    total = len(candidates)
    logger.info("Companion: loading %d referenced document(s)", total)

    for i, candidate in enumerate(candidates, 1):
        ext = candidate.suffix.lower()
        if ext in TEXT_EXTENSIONS:
            text = _load_text_file(candidate)
            if text:
                logger.info("Companion: loading reference %d/%d: %s", i, total, candidate.name)
                references[candidate.name] = text
        elif ext in CONVERTIBLE_EXTENSIONS:
            logger.info("Companion: converting reference %d/%d: %s", i, total, candidate.name)
            text = _convert_document(candidate, output_dir, settings)
            if text:
                references[candidate.name] = text
        else:
            logger.debug("Companion: skipping unsupported reference: %s", candidate.name)

    return references


def build_companion_bundle(companion_path: Path, output_dir: Path, settings: Settings) -> str:
    """Load companion .md and all resolved references into a formatted bundle."""
    content = companion_path.read_text()
    references = resolve_references(companion_path, content, output_dir, settings)

    parts = [f"## Companion Notes: {companion_path.name}\n\n{content}"]
    for filename, ref_content in references.items():
        parts.append(f"\n\n## Referenced Document: {filename}\n\n{ref_content}")

    return "\n".join(parts)


def analyze_companion(bundle: str, provider: str, model: str, api_key: str) -> str:
    """Run lightweight LLM pre-analysis on the companion bundle.

    Extracts attendees, agenda, key terms, referenced doc summaries.
    """
    from doc_convert.providers import PROVIDER_URLS  # noqa: PLC0415

    with trace_span("companion.analyze", provider=provider, model=model):
        logger.info("Companion: analyzing context with %s/%s", provider, model)
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                PROVIDER_URLS[provider],
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": COMPANION_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Extract context from these notes:\n\n{bundle}"},
                    ],
                },
            )
            resp.raise_for_status()

    return resp.json()["choices"][0]["message"]["content"]


def load_companion_context(
    source_path: Path,
    output_dir: Path,
    use_external_llm: str | None,
    settings: Settings,
    *,
    force: bool = False,
    name_override: str | None = None,
) -> str | None:
    """Main entry point: detect, load, analyze, cache, and return companion context.

    Called from cli.py before dispatching to converters.
    Any failure returns None (graceful degradation, conversion proceeds normally).

    Args:
        source_path: The file being converted
        output_dir: The _docling/ output directory
        use_external_llm: Provider/model override
        settings: Application settings
        force: Force re-analysis even if cached
        name_override: For --start-audio, the name to search for (e.g. "Meeting Name")
    """
    try:
        companion_path = detect_companion(source_path, name_override=name_override)
        if companion_path is None:
            return None

        logger.info("Companion file found: %s", companion_path.name)

        # Check cache
        cache_path = output_dir / "companion_context.md"
        if cache_path.exists() and not force:
            logger.info("Companion: using cached context from %s", cache_path.name)
            return cache_path.read_text()

        # Build bundle (companion + referenced files)
        output_dir.mkdir(parents=True, exist_ok=True)
        bundle = build_companion_bundle(companion_path, output_dir, settings)

        # Analyze with lightweight LLM
        from doc_convert.providers import resolve_media_llm  # noqa: PLC0415

        provider, model, api_key = resolve_media_llm(use_external_llm, settings)
        context = analyze_companion(bundle, provider, model, api_key)

        # Cache result
        cache_path.write_text(context)
        logger.info("Companion: context cached to %s", cache_path)
        console.print(f"  [dim]companion_context.md (from {companion_path.name})[/dim]")

        return context
    except Exception:
        logger.warning("Failed to load companion context for %s, proceeding without it", source_path.name)
        return None
