"""``--meeting-summary`` runtime: produce an HTML brief from an audio document.md.

Audio-only by design. Reads the final ``document.md`` (after companion-derived
``## Additional Context`` / ``## Screenshots`` / ``## Additional Documents``
sections have been merged in), asks the analysis LLM to render an HTML brief
following the format in :func:`providers.get_meeting_summary_prompt`, writes
``summary.html`` next to ``document.md`` and opens it via ``open``.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

import httpx

from doc_convert.providers import (
    get_meeting_summary_prompt,
    get_provider_url,
    resolve_document_analysis_llm,
)
from tracing import trace_span

if TYPE_CHECKING:
    from pathlib import Path

    from config import Settings

logger = logging.getLogger(__name__)


def generate_meeting_summary(
    output_dir: Path,
    settings: Settings,
    *,
    analyze_model: str | None,
    meeting_context: str | None = None,
    lang: str | None = None,
    open_after: bool = True,
) -> bool:
    """Write ``summary.html`` from ``document.md``. Returns True on success."""
    doc_md = output_dir / "document.md"
    if not doc_md.exists():
        logger.warning("Cannot summarize: %s does not exist", doc_md)
        return False

    provider, model, api_key = resolve_document_analysis_llm(analyze_model, settings)
    system = get_meeting_summary_prompt()
    if meeting_context:
        system = f"Meeting Context:\n{meeting_context}\n\n{system}"
    if lang:
        rule = f"HARD RULE: Render every textual content in {lang}."
        system = f"{rule}\n\n{system}\n\n{rule}"

    user_msg = f"Transcript and supporting material:\n\n{doc_md.read_text()}"

    with trace_span("meeting_summary.generate", file=str(doc_md), provider=provider):
        logger.info("Generating meeting summary with %s/%s", provider, model)
        with httpx.Client(timeout=300.0) as client:
            resp = client.post(
                get_provider_url(provider, settings),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
            resp.raise_for_status()
    html = resp.json()["choices"][0]["message"]["content"]

    summary_path = output_dir / "summary.html"
    summary_path.write_text(html)
    logger.info("Meeting summary written to %s", summary_path)

    if open_after:
        try:
            subprocess.run(["open", str(summary_path)], check=False)
        except FileNotFoundError:
            logger.debug("`open` command not found; skipping auto-open")

    return True
