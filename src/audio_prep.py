"""Audio preparation for inline-base64 LLM providers (IBM ICA, OpenRouter).

Inline providers cap the request body around ~75 MB of base64, i.e. ~56 MB of
raw audio (measured against IBM ICA: 79 MB base64 OK, 133 MB -> 502, 200 MB ->
413). To keep audio transcription on such a provider we:

1. Normalise the source to mono 16 kHz OGG Opus at 32 kbps (a voice profile,
   ~14 MB/h). Opus is far more compact than Vorbis at equal speech quality and
   is available in every ffmpeg build here (libvorbis is not). This shrinks
   mp3/opus/m4a/wav down enough that any recording up to ~3.5 h fits one request.
2. Split the recording into equal parts with a one-minute overlap when it is
   still too large OR too long. Inline providers sit behind a gateway that aborts
   a request after ~10 minutes (Cloudflare 524), and transcribing a long
   recording exceeds that even when the file is small (a 2 h mono Opus recording
   is only ~20 MB), so parts are capped by DURATION as well as size. No word is
   lost at a seam and speaker labels can be carried over.

Kept separate from ``audio.py`` (which owns recording + prompts) so the size
handling can be unit-tested without touching the recording path.
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003

logger = logging.getLogger(__name__)

# Stay comfortably under the measured ~56 MB raw / ~75 MB base64 inline ceiling.
SIZE_LIMIT_MB = 50
# Inline providers (IBM ICA, OpenRouter) sit behind a gateway that aborts a long
# request, either with a 524/504 status or by hanging until the client read
# timeout fires (~10 min). Transcribing a long recording in one shot exceeds that
# even when the file is small (a 2 h mono Opus recording is only ~20 MB but takes
# far longer to transcribe), so we also cap each request by DURATION. Measured:
# 25 min parts transcribe reliably, 29 min parts intermittently hang, so we keep
# a safe 20 min ceiling.
DURATION_LIMIT_SECONDS = 20 * 60
# google/ has no gateway cap, but the model still can't verbatim-transcribe a
# huge single shot: a whole 2 h recording (~137k audio tokens) comes back as an
# empty MALFORMED_RESPONSE. So google also splits by duration, just with a larger
# ceiling (no 10-min gateway to respect, only the model's single-call limit).
GOOGLE_DURATION_LIMIT_SECONDS = 30 * 60
# One-minute overlap between consecutive parts (words at a hard cut survive in
# the neighbouring part; also gives the model context to keep speakers stable).
OVERLAP_SECONDS = 60
# Voice profile: mono, 16 kHz, Opus @ 32 kbps (compact, high speech quality).
TARGET_SAMPLE_RATE = 16000
TARGET_CHANNELS = 1
TARGET_BITRATE = "32k"


@dataclass(frozen=True)
class AudioPart:
    """One transcodable slice of a recording (a whole file when not split)."""

    path: Path
    index: int  # 0-based
    start_s: float
    end_s: float


def _ffprobe(path: Path, entries: str, *, select_audio: bool = False) -> list[str]:
    """Return ``ffprobe`` values for ``entries`` (comma-separated) as lines."""
    cmd = ["ffprobe", "-v", "error"]
    if select_audio:
        cmd += ["-select_streams", "a:0"]
    cmd += ["-show_entries", entries, "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def audio_duration_seconds(path: Path) -> float | None:
    """Return the audio duration in seconds via ``ffprobe``, or None on failure."""
    values = _ffprobe(path, "format=duration")
    try:
        return float(values[0])
    except (IndexError, ValueError):
        logger.warning("ffprobe could not read duration for %s", path.name)
        return None


def needs_transcode(path: Path) -> bool:
    """True unless ``path`` is already a mono OGG at <= 16 kHz (our target)."""
    if path.suffix.lower() != ".ogg":
        return True
    values = _ffprobe(path, "stream=channels,sample_rate", select_audio=True)
    try:
        channels, sample_rate = int(values[0]), int(values[1])
    except (IndexError, ValueError):
        return True
    return not (channels == TARGET_CHANNELS and sample_rate <= TARGET_SAMPLE_RATE)


def _encode_args(src: Path, dest: Path, *, ss: float | None = None, t: float | None = None) -> list[str]:
    """Build an ffmpeg command that (re)encodes ``src`` to mono 16 kHz OGG Opus."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if ss is not None:
        cmd += ["-ss", str(ss)]
    cmd += ["-i", str(src)]
    if t is not None:
        cmd += ["-t", str(t)]
    cmd += [
        "-ac",
        str(TARGET_CHANNELS),
        "-ar",
        str(TARGET_SAMPLE_RATE),
        "-c:a",
        "libopus",
        "-b:a",
        TARGET_BITRATE,
        str(dest),
    ]
    return cmd


def transcode_to_ogg(src: Path, dest: Path) -> Path:
    """Transcode ``src`` to a mono 16 kHz OGG Opus voice file at ``dest``."""
    subprocess.run(_encode_args(src, dest), check=True)
    return dest


def plan_parts(
    duration_s: float,
    size_mb: float,
    *,
    limit_mb: float = SIZE_LIMIT_MB,
    duration_limit_s: float = DURATION_LIMIT_SECONDS,
    overlap_s: float = OVERLAP_SECONDS,
) -> list[tuple[float, float]]:
    """Return ``(start, end)`` spans covering the recording, or ``[]`` if it fits.

    Splits into enough equal parts to satisfy BOTH ceilings: the base64 payload
    size (``limit_mb``) and the per-request duration (``duration_limit_s``, which
    keeps each transcription under the inline provider's gateway timeout). Every
    part after the first starts ``overlap_s`` earlier than its slice, giving a
    fixed overlap between consecutive parts.
    """
    if duration_s <= 0:
        return []
    n_size = math.ceil(size_mb / limit_mb) if size_mb > limit_mb else 1
    n_dur = math.ceil(duration_s / duration_limit_s) if duration_s > duration_limit_s else 1
    n = max(n_size, n_dur)
    if n <= 1:
        return []
    seg = duration_s / n
    spans: list[tuple[float, float]] = []
    for i in range(n):
        start = max(0.0, i * seg - overlap_s) if i > 0 else 0.0
        end = min(duration_s, (i + 1) * seg)
        spans.append((start, end))
    return spans


def prepare_audio(
    source: Path,
    work_dir: Path,
    *,
    size_limit_mb: float = SIZE_LIMIT_MB,
    duration_limit_s: float = DURATION_LIMIT_SECONDS,
    overlap_s: float = OVERLAP_SECONDS,
) -> list[AudioPart]:
    """Normalise ``source`` under ``work_dir`` and split it if still oversized.

    Writes ``work_dir/audio.ogg`` (the normalised recording) and, when a split
    is needed, ``work_dir/parts/part_NN.ogg``. Returns an ordered list of
    ``AudioPart`` (length 1 when the whole recording fits one request). A part is
    emitted per ``size_limit_mb`` of payload AND per ``duration_limit_s`` of
    audio, whichever demands more parts.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    normalized = work_dir / "audio.ogg"
    if needs_transcode(source):
        logger.info("Transcoding %s -> mono 16 kHz ogg", source.name)
        transcode_to_ogg(source, normalized)
    elif source.resolve() != normalized.resolve():
        normalized.write_bytes(source.read_bytes())

    size_mb = normalized.stat().st_size / (1024 * 1024)
    duration = audio_duration_seconds(normalized) or 0.0
    spans = plan_parts(
        duration, size_mb, limit_mb=size_limit_mb, duration_limit_s=duration_limit_s, overlap_s=overlap_s
    )
    if not spans:
        return [AudioPart(normalized, 0, 0.0, duration)]

    parts_dir = work_dir / "parts"
    parts_dir.mkdir(exist_ok=True)
    parts: list[AudioPart] = []
    for i, (start, end) in enumerate(spans):
        chunk = parts_dir / f"part_{i + 1:02d}.ogg"
        subprocess.run(_encode_args(normalized, chunk, ss=start, t=end - start), check=True)
        parts.append(AudioPart(chunk, i, start, end))
    logger.info(
        "Split %s (%.0fs, %.0f MB) into %d parts with %ds overlap",
        source.name,
        duration,
        size_mb,
        len(parts),
        int(overlap_s),
    )
    return parts
