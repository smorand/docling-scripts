"""Audio recording and transcription.

Recording uses sox (must be installed: brew install sox).
Transcription and analysis use external LLM via media_llm.
"""

from __future__ import annotations

import logging
import signal
import subprocess
from pathlib import Path  # noqa: TC003

from logging_config import console

logger = logging.getLogger(__name__)

TRANSCRIPTION_PROMPT = (
    "Transcribe this audio recording into structured meeting minutes with speaker diarization.\n\n"
    "Rules:\n"
    "- The 'Meeting context' in this system prompt (if any) is BACKGROUND ONLY. "
    "Use it to spell names of people, projects, products, and technical terms "
    "consistently. DO NOT include any of that context in your output.\n"
    "- First, identify how many distinct speakers are present by listening to the voices. "
    "Assign each speaker a consistent label throughout the transcript (never collapse "
    "two speakers into one).\n"
    "- If a speaker identifies themselves by name and you are confident, use their real name. "
    "Match the spelling from the meeting context (e.g. 'Aarti' not 'Arti'). Otherwise use "
    "'Speaker 1', 'Speaker 2', etc.\n"
    "- Add an approximate timestamp at every speaker change in the form [MM:SS]. "
    "Round to the nearest 5 seconds.\n"
    "- Preserve the original language. Transcribe verbatim with light cleanup of filler "
    "words that don't carry meaning.\n"
    "- Format as markdown with exactly two top-level sections.\n\n"
    "Output format:\n"
    "## Attendees\n"
    "- Speaker 1 (or real name if confidently identified)\n"
    "- Speaker 2\n\n"
    "## Minutes\n"
    "[00:00] **Speaker 1**: verbatim text...\n"
    "[00:42] **Speaker 2**: verbatim text...\n"
    "[01:17] **Speaker 1**: ...\n"
)

ANALYSIS_PROMPT = (
    "You are producing structured meeting minutes from a recording, with optional "
    "companion notes and attached whiteboard/screen images supplied via the meeting "
    "context. The reader did not attend; the output must be self-contained, specific, "
    "and actionable.\n\n"
    "GLOBAL RULES\n"
    "- Your output MUST begin with the line '# Meeting: <title>' and nothing before "
    "it. DO NOT prepend any agenda, topic list, key-terms section, or summary of the "
    "meeting context. The companion notes are BACKGROUND ONLY; never echo them.\n"
    "- Write in the language of the recording (or the explicit --lang).\n"
    "- Be substantive: prefer extracted facts (names, numbers, terms, %, deadlines, "
    "product/project names) over vague summaries.\n"
    "- Use the companion notes as ground truth for spelling of names, projects, and "
    "technical terms. Use consistent spelling throughout (e.g. 'Aarti' not 'Arti').\n"
    "- Integrate attached images: when a fact comes from a whiteboard or slide, cite "
    "it inline as '(from whiteboard, image 01)' using the image number/filename from "
    "the meeting context.\n"
    "- DO NOT include per-bullet timestamps like *(Speaker, ~MM:SS)*. They add noise.\n"
    "- Skip any section header that would be empty. Do not write 'N/A' or 'None'.\n"
    "- Do not invent numbers, names, or dates that are not in the recording or the "
    "companion notes.\n\n"
    "OUTPUT STRUCTURE (in this order; drop any section without content)\n\n"
    "# Meeting: <crisp title derived from content>\n\n"
    "## Meeting Information\n"
    "- Date, time, duration, type (workshop / weekly / one-to-one / steering / etc.), "
    "project or topic.\n\n"
    "## Attendees\n"
    "| Name | Role | Status |\n"
    "|------|------|--------|\n"
    "Use Status = 'Present', 'Mentioned (async review)', or 'Left early at ~MM:SS' "
    "when known. Infer Role from how they speak (Facilitator, SME / Subject Matter "
    "Expert, Technical lead, Platform, etc.).\n\n"
    "## Executive Summary\n"
    "3-6 sentences: what was discussed, what was decided, what remains open. Lead "
    "with the headline outcome.\n\n"
    "## Reference Pipeline / Process / Architecture\n"
    "ONLY when an end-to-end flow or process was walked through. Reproduce as a "
    "numbered list. Cite whiteboards when applicable.\n\n"
    "## Target Audience / Scope / Stack\n"
    "ONLY when explicitly discussed. Crisp statement.\n\n"
    "## <Options discussed: use cases / candidates / scenarios>\n"
    "For each item, use this sub-template:\n"
    "### <Name>, <one-line tag>\n"
    "- **Persona / Context:**\n"
    "- **Scenario:**\n"
    "- **Expected platform behavior or queries:**\n"
    "- **Complexity / Failure modes:**\n"
    "- **Fit with overall goal / Stack coverage:**\n\n"
    "## Guardrails / UX / Non-functional constraints\n"
    "ONLY when discussed. Bullets, integrate whiteboards when applicable.\n\n"
    "## Selection Criteria\n"
    "ONLY when explicit criteria were stated. Bullets.\n\n"
    "## Decisions\n"
    "| # | Decision | Rationale | Impact |\n"
    "|---|----------|-----------|--------|\n"
    "Capture EVERY closing of a discussion thread, not only explicit "
    "'we decided X' statements. Include:\n"
    "- Persona / scope / audience choices accepted by silent agreement.\n"
    "- Tooling and integration picks ('we'll use X for Y').\n"
    "- Strategy choices ('one comprehensive use case, not five module demos').\n"
    "- Vocabulary / naming choices ('expose to users as K-Graph').\n"
    "Target 3-6 decisions per substantive meeting. If you find fewer than 3 in a "
    "rich workshop, you are under-extracting; re-read the recording.\n"
    "Each row must justify itself: Rationale = the reason the team converged; "
    "Impact = High / Medium / Low based on what it constrains downstream.\n\n"
    "## Risks and Open Points\n"
    "| # | Item | Severity | Mitigation |\n"
    "|---|------|----------|------------|\n\n"
    "## Action Items\n"
    "| # | Action | Owner | Due | Priority |\n"
    "|---|--------|-------|-----|----------|\n\n"
    "## Sentiment and Dynamics\n"
    "- Overall tone, alignment between participants, friction points, the single "
    "main open question.\n\n"
    "## Recommendations\n"
    "MANDATORY when 2+ alternatives were discussed without a final pick, or when a "
    "strategic choice was deferred. Three forms count:\n"
    "1. Pick a candidate ('favor option X because Y').\n"
    "2. Lock down a spec before it becomes a blocker.\n"
    "3. Promote a useful artifact (whiteboard, draft, naming) as canonical going "
    "forward.\n"
    "Be opinionated and grounded in what was said. Avoid generic advice.\n\n"
    "## Attachments\n"
    "List every companion note and image referenced in the meeting context with a "
    "one-line description.\n\n"
    "DO NOT add a 'Next Steps' section. Action items + Recommendations are enough.\n"
)


def check_sox() -> None:
    """Verify sox is installed."""
    try:
        subprocess.run(["sox", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        console.print("[red]sox is not installed. Install with: brew install sox[/red]")
        raise SystemExit(1) from None


def record_audio(output_path: Path) -> Path:
    """Record audio from microphone using sox. Ctrl+C stops recording.

    Args:
        output_path: Where to save the recorded audio (e.g. name_docling/audio.ogg).

    Returns the path to the recorded file.
    """
    check_sox()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Recording to %s (Ctrl+C to stop)", output_path)
    console.print(f"[bold green]Recording...[/bold green] {output_path}")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    proc = subprocess.Popen(
        ["sox", "-d", "-c", "1", "-r", "16000", str(output_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        proc.wait()
        console.print(f"\n[green]Recording saved:[/green] {output_path}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        console.print("[red]Recording failed or empty[/red]")
        raise SystemExit(1)

    return output_path


def _get_transcription_prompt() -> str:
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("audio", "transcription_system_prompt", TRANSCRIPTION_PROMPT)


def _get_analysis_prompt() -> str:
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("audio", "analysis_system_prompt", ANALYSIS_PROMPT)


def build_transcription_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for transcription."""
    system = _get_transcription_prompt()
    prompt = "Transcribe this audio recording."
    if meeting_name:
        prompt = f"Transcribe this audio recording. Meeting: {meeting_name}"
        system = f"Meeting context: {meeting_name}\n\n{system}"
    return prompt, system


def build_analysis_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for analysis."""
    system = _get_analysis_prompt()
    prompt = "Analyze this audio recording and produce a structured summary."
    if meeting_name:
        prompt = f"Analyze this audio recording. Meeting: {meeting_name}"
        system = f"Meeting context: {meeting_name}\n\n{system}"
    return prompt, system
