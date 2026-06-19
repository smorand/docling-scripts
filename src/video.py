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
    "Speaker identification rules:\n"
    "- If a speaker identifies themselves by name and you are confident, use their real name.\n"
    "- If you are NOT confident about a speaker's identity, use generic labels "
    "(Speaker 1, Speaker 2, etc.). The user will replace them later.\n\n"
    "Include the following sections:\n\n"
    "## Summary\n"
    "2 to 5 paragraph overview of the video content\n\n"
    "## Transcription\n"
    "Full speech/dialogue with timestamps [HH:MM:SS] and speaker attribution\n\n"
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
    "Start with a metadata header:\n"
    "```\n"
    "**Source:** <filename>\n"
    "**Format:** Video\n"
    "**Type:** <content type: demo, presentation, tutorial, meeting recording, webinar, etc.>\n"
    "**Date:** <date if identifiable from content or filename>\n"
    "```\n\n"
    "For each key point, include the approximate timestamp in the format "
    "*(~HH:MM:SS)* and the speaker name if identifiable, so the reader can "
    "locate the original moment in the video.\n\n"
    "Include the following sections:\n\n"
    "## Executive Summary\n"
    "Concise overview of the video's key message and purpose\n\n"
    "## Key Takeaways\n"
    "Numbered list of the most important points with timestamps *(~HH:MM:SS)*\n\n"
    "## Detailed Analysis\n"
    "In-depth breakdown with speaker attribution and timestamps *(Speaker, ~HH:MM:SS)*\n\n"
    "## Action Items\n"
    "| Item | Priority | Details | Source |\n"
    "|------|----------|--------|--------|\n\n"
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


def _get_extraction_prompt() -> str:
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("video", "extraction_system_prompt", EXTRACTION_PROMPT)


def _get_analysis_prompt() -> str:
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("video", "analysis_system_prompt", ANALYSIS_PROMPT)


def build_extraction_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for video content extraction."""
    system = _get_extraction_prompt()
    prompt = "Extract all content from this video into structured markdown."
    if meeting_name:
        prompt = f"Extract all content from this video. Context: {meeting_name}"
        system = f"Video context: {meeting_name}\n\n{system}"
    return prompt, system


def build_analysis_prompt(meeting_name: str | None = None) -> tuple[str, str | None]:
    """Build prompt and system prompt for video analysis."""
    system = _get_analysis_prompt()
    prompt = "Analyze this video and produce an executive summary."
    if meeting_name:
        prompt = f"Analyze this video. Context: {meeting_name}"
        system = f"Video context: {meeting_name}\n\n{system}"
    return prompt, system


# ── Long-video chunking ────────────────────────────────────────────────────

CHUNK_THRESHOLD_SECONDS = 30 * 60  # videos longer than this are split
CHUNK_LENGTH_SECONDS = 30 * 60


def video_duration_seconds(path: Path) -> float | None:
    """Return the video duration in seconds via ``ffprobe``, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        logger.warning("ffprobe failed for %s; treating as short video", path.name)
        return None


def chunk_video(path: Path, chunk_seconds: int = CHUNK_LENGTH_SECONDS) -> list[Path]:
    """Split a video into ``chunk_seconds``-long chunks via ffmpeg stream copy.

    Stream copy (-c copy) avoids re-encoding, so chunking a 2 h video takes
    seconds. Chunks land in a temporary directory; callers are responsible for
    cleanup (or just leave them: the OS reaps /tmp).
    """
    duration = video_duration_seconds(path)
    if duration is None or duration <= chunk_seconds:
        return [path]

    tmp_dir = Path(tempfile.mkdtemp(prefix="docconvert_video_chunks_"))
    chunk_paths: list[Path] = []
    start = 0.0
    index = 0
    while start < duration:
        chunk_path = tmp_dir / f"chunk_{index:03d}{path.suffix}"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-ss",
                str(start),
                "-i",
                str(path),
                "-t",
                str(chunk_seconds),
                "-c",
                "copy",
                str(chunk_path),
            ],
            check=True,
        )
        chunk_paths.append(chunk_path)
        start += chunk_seconds
        index += 1

    logger.info("Split %s (%.0fs) into %d chunk(s) of %ds", path.name, duration, len(chunk_paths), chunk_seconds)
    return chunk_paths


def format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS for chunk headings."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


META_SUMMARY_PROMPT = (
    "You are given the per-chunk summaries of a long video, in chronological order. "
    "Produce a single Executive Summary (4 to 8 sentences) that synthesizes the "
    "whole video: main topics covered, key decisions or findings, and the overall "
    "arc from beginning to end. Reference chunk numbers when citing specific moments "
    "(e.g. 'in chunk 2 ...'). Output only the summary, no preamble."
)


def get_meta_summary_prompt() -> str:
    from doc_convert.prompt_config import get_prompt  # noqa: PLC0415

    return get_prompt("video", "meta_summary_system_prompt", META_SUMMARY_PROMPT)
