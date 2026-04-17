"""Audio recording and transcription.

Recording uses sox (must be installed: brew install sox).
Transcription and analysis use external LLM via media_llm.
"""

from __future__ import annotations

import logging
import signal
import subprocess
from datetime import datetime
from pathlib import Path

from logging_config import console

logger = logging.getLogger(__name__)

RECORDS_DIR = Path.home() / "Downloads" / "Records"

TRANSCRIPTION_PROMPT = (
    "Transcribe this audio recording into structured meeting minutes.\n\n"
    "Rules:\n"
    "- Identify each speaker by their voice and assign them a consistent name "
    "(Speaker 1, Speaker 2, etc.)\n"
    "- Preserve the original language of the recording\n"
    "- Transcribe verbatim, do not summarize\n"
    "- Format as markdown with Attendees and Minutes sections\n\n"
    "Output format:\n"
    "## Attendees\n"
    "- Speaker 1\n"
    "- Speaker 2\n\n"
    "## Minutes\n"
    "- **Speaker 1**: verbatim text...\n"
    "- **Speaker 2**: verbatim text...\n"
)

ANALYSIS_PROMPT = (
    "Analyze this audio recording and produce a structured meeting summary.\n\n"
    "Output the following sections in markdown:\n\n"
    "## Meeting Overview\n"
    "Date, duration, type, attendees\n\n"
    "## Key Discussion Points\n"
    "Main topics discussed with important quotes\n\n"
    "## Decisions Made\n"
    "Each decision with context\n\n"
    "## Action Items\n"
    "| Task | Assigned To | Due Date |\n"
    "|------|------------|----------|\n\n"
    "## Next Steps\n"
    "Follow-ups, outstanding questions, next meeting\n"
)


def check_sox() -> None:
    """Verify sox is installed."""
    try:
        subprocess.run(["sox", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        console.print("[red]sox is not installed. Install with: brew install sox[/red]")
        raise SystemExit(1) from None


def record_audio(label: str | None = None) -> Path:
    """Record audio from microphone using sox. Ctrl+C stops recording.

    Returns the path to the recorded file.
    """
    check_sox()

    RECORDS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H:%M:%S")
    safe_label = (label or "recording").replace(" ", "_")
    filename = f"{timestamp}_{safe_label}.ogg"
    filepath = RECORDS_DIR / filename

    logger.info("Recording to %s (Ctrl+C to stop)", filepath)
    console.print(f"[bold green]Recording...[/bold green] {filepath.name}")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    proc = subprocess.Popen(
        ["sox", "-d", "-c", "1", "-r", "16000", str(filepath)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.send_signal(signal.SIGINT)
        proc.wait()
        console.print(f"\n[green]Recording saved:[/green] {filepath}")

    if not filepath.exists() or filepath.stat().st_size == 0:
        console.print("[red]Recording failed or empty[/red]")
        raise SystemExit(1)

    return filepath


def build_transcription_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for transcription."""
    system = TRANSCRIPTION_PROMPT
    prompt = "Transcribe this audio recording."
    if meeting_name:
        prompt = f"Transcribe this audio recording. Meeting: {meeting_name}"
        system = f"Meeting context: {meeting_name}\n\n{system}"
    return prompt, system


def build_analysis_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for analysis."""
    system = ANALYSIS_PROMPT
    prompt = "Analyze this audio recording and produce a structured summary."
    if meeting_name:
        prompt = f"Analyze this audio recording. Meeting: {meeting_name}"
        system = f"Meeting context: {meeting_name}\n\n{system}"
    return prompt, system
