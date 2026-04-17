"""Video processing and YouTube download.

Video analysis uses external LLM via media_llm.
YouTube download uses yt-dlp (must be installed: brew install yt-dlp).
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path

from logging_config import console

logger = logging.getLogger(__name__)

YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([a-zA-Z0-9_-]+)"
)

EXTRACTION_PROMPT = (
    "Analyze this video and produce a structured markdown document.\n\n"
    "Include the following sections:\n\n"
    "## Summary\n"
    "2 to 5 paragraph overview of the video content\n\n"
    "## Transcription\n"
    "Full speech/dialogue with timestamps [HH:MM:SS]\n\n"
    "## Scene Descriptions\n"
    "Timestamped visual descriptions of key scenes\n\n"
    "## Key Topics\n"
    "Bulleted list of main themes and subjects\n\n"
    "## Visual Elements\n"
    "On-screen text, logos, charts, diagrams\n\n"
    "## Metadata\n"
    "Duration, language, any other relevant technical details\n"
)

ANALYSIS_PROMPT = (
    "Analyze this video in depth and produce an executive summary.\n\n"
    "Include the following sections:\n\n"
    "## Executive Summary\n"
    "Concise overview of the video's key message and purpose\n\n"
    "## Key Takeaways\n"
    "Numbered list of the most important points\n\n"
    "## Detailed Analysis\n"
    "In-depth breakdown of the content, arguments, and evidence presented\n\n"
    "## Action Items\n"
    "| Item | Priority | Details |\n"
    "|------|----------|--------|\n\n"
    "## Recommendations\n"
    "What to do with the information from this video\n"
)


def is_youtube_url(source: str) -> bool:
    """Check if the source is a YouTube URL."""
    return bool(YOUTUBE_RE.search(source))


def check_ytdlp() -> None:
    """Verify yt-dlp is installed."""
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        console.print("[red]yt-dlp is not installed. Install with: brew install yt-dlp[/red]")
        raise SystemExit(1) from None


def download_youtube(url: str) -> Path:
    """Download a YouTube video using yt-dlp. Returns path to downloaded file."""
    check_ytdlp()

    tmp_dir = tempfile.mkdtemp(prefix="doc-convert-yt-")
    output_template = f"{tmp_dir}/%(title)s.%(ext)s"

    logger.info("Downloading YouTube video: %s", url)
    console.print("[bold]Downloading YouTube video...[/bold]")

    result = subprocess.run(
        [
            "yt-dlp",
            "-f",
            "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "-o",
            output_template,
            "--no-playlist",
            "--print",
            "after_move:filepath",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    if result.returncode != 0:
        console.print(f"[red]yt-dlp failed: {result.stderr.strip()}[/red]")
        raise SystemExit(1)

    filepath = Path(result.stdout.strip().split("\n")[-1])
    if not filepath.exists():
        console.print("[red]Downloaded file not found[/red]")
        raise SystemExit(1)

    logger.info("Downloaded: %s", filepath)
    console.print(f"[green]Downloaded:[/green] {filepath.name}")
    return filepath


def build_extraction_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for video content extraction."""
    system = EXTRACTION_PROMPT
    prompt = "Extract all content from this video into structured markdown."
    if meeting_name:
        prompt = f"Extract all content from this video. Context: {meeting_name}"
        system = f"Video context: {meeting_name}\n\n{system}"
    return prompt, system


def build_analysis_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for video analysis."""
    system = ANALYSIS_PROMPT
    prompt = "Analyze this video and produce an executive summary."
    if meeting_name:
        prompt = f"Analyze this video. Context: {meeting_name}"
        system = f"Video context: {meeting_name}\n\n{system}"
    return prompt, system
