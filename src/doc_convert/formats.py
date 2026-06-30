"""Format detection, captions spec, and engine selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path  # noqa: TC003

import typer
from docling.datamodel.base_models import InputFormat

from config import Settings  # noqa: TC001
from logging_config import console

EXT_TO_FORMAT: dict[str, InputFormat] = {
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
    ".xlsx": InputFormat.XLSX,
    ".pptx": InputFormat.PPTX,
    ".pptm": InputFormat.PPTX,
    ".potx": InputFormat.PPTX,
    ".ppsx": InputFormat.PPTX,
    ".jpg": InputFormat.IMAGE,
    ".jpeg": InputFormat.IMAGE,
    ".png": InputFormat.IMAGE,
    ".tiff": InputFormat.IMAGE,
    ".tif": InputFormat.IMAGE,
    ".bmp": InputFormat.IMAGE,
    ".webp": InputFormat.IMAGE,
}


def detect_format(path: Path) -> InputFormat:
    """Detect input format from file extension."""
    ext = path.suffix.lower()
    fmt = EXT_TO_FORMAT.get(ext)
    if fmt is None:
        console.print(f"[red]Unsupported extension '{ext}'[/red]")
        console.print(f"Supported: {', '.join(sorted(EXT_TO_FORMAT.keys()))}")
        raise typer.Exit(1)
    return fmt


class Engine(str, Enum):
    """Body extraction engine for PDF / image inputs."""

    LOCAL = "local"
    LLM = "llm"


LOCAL_PRESETS: tuple[str, ...] = ("smolvlm", "granite_vision", "pixtral", "qwen")
DEFAULT_LOCAL_PRESET = "smolvlm"

PRESET_REPO_IDS: dict[str, str] = {
    "smolvlm": "HuggingFaceTB/SmolVLM-256M-Instruct",
    "granite_vision": "ibm-granite/granite-vision-3.3-2b",
    "pixtral": "mistral-community/pixtral-12b",
    "qwen": "Qwen/Qwen2.5-VL-3B-Instruct",
}

# Preferred cloud captioner when neither --captions nor --llm is given.
# Tried in order; first one with credentials in env is used. Falls back to
# the local smolvlm preset if none has credentials.
AUTO_CAPTIONS_PREFERENCES: tuple[str, ...] = (
    "ibm/claude-haiku-4-5",
    "google/gemini-3.1-flash-lite-preview",
)


@dataclass(frozen=True)
class CaptionsOff:
    """No figure descriptions."""


@dataclass(frozen=True)
class CaptionsLocal:
    """Local VLM captioner (offline HuggingFace model)."""

    preset: str


@dataclass(frozen=True)
class CaptionsLlm:
    """Remote LLM captioner (provider/model)."""

    provider: str
    model: str


CaptionsSpec = CaptionsOff | CaptionsLocal | CaptionsLlm


# ── OCR engine selection (--ocr-model) ───────────────────────────────────
# OCR is the per-region "text from bitmap" stage of the local PDF pipeline.
# It only fires on scanned/image content; born-digital text bypasses it.
LOCAL_OCR_ENGINES: tuple[str, ...] = ("tesseract",)
DEFAULT_OCR_ENGINE = "tesseract"
# Default OCR model when --ocr-model is omitted: a cloud LLM reads scanned/image
# regions. OCR only fires on bitmap content, so born-digital PDFs cost nothing.
# Use --ocr-model local for the offline Tesseract CLI engine instead.
DEFAULT_OCR_MODEL = "google/gemini-3.1-pro-preview"


@dataclass(frozen=True)
class OcrOff:
    """OCR disabled; only the embedded text layer is used."""


@dataclass(frozen=True)
class OcrLocal:
    """Local docling OCR engine (Tesseract CLI)."""

    engine: str = DEFAULT_OCR_ENGINE


@dataclass(frozen=True)
class OcrLlm:
    """Remote LLM OCR: each image region is read by a cloud model (provider/model)."""

    provider: str
    model: str


OcrSpec = OcrOff | OcrLocal | OcrLlm


def parse_ocr_model(value: str) -> OcrSpec:
    """Parse an --ocr-model value: 'off', a local engine name, or a 'provider/model' slug."""
    from doc_convert.providers import parse_external_llm  # noqa: PLC0415

    if value == "off":
        return OcrOff()
    if "/" in value:
        provider, model = parse_external_llm(value)
        return OcrLlm(provider, model)
    if value == "local" or value in LOCAL_OCR_ENGINES:
        return OcrLocal(DEFAULT_OCR_ENGINE)
    engines = ", ".join(LOCAL_OCR_ENGINES)
    console.print(
        f"[red]Invalid --ocr-model value '{value}'. Expected: off, a local engine "
        f"({engines}) or 'local', or a 'provider/model' slug.[/red]"
    )
    raise typer.Exit(1)


def resolve_ocr_model(value: str | None, *, no_ocr: bool) -> OcrSpec:
    """Resolve the OCR spec, applying defaults.

    Precedence:
        1. ``--no-ocr`` (legacy flag) forces OCR off.
        2. Explicit ``--ocr-model`` value (off, local engine, or provider/model).
        3. Default: the cloud LLM in ``DEFAULT_OCR_MODEL`` (Gemini). OCR only
           fires on scanned/image regions, so born-digital PDFs incur no cost
           or credential requirement. Use ``--ocr-model local`` for Tesseract.

    Note: unlike ``--captions``, OCR does NOT auto-inherit ``--llm``. The OCR
    model is chosen by this axis alone.
    """
    from doc_convert.providers import parse_external_llm  # noqa: PLC0415

    if no_ocr:
        return OcrOff()
    if value is not None:
        return parse_ocr_model(value)
    provider, model = parse_external_llm(DEFAULT_OCR_MODEL)
    return OcrLlm(provider, model)


def parse_captions(value: str) -> CaptionsSpec:
    """Parse a --captions value: 'off', a local preset, or a 'provider/model' slug."""
    from doc_convert.providers import parse_external_llm  # noqa: PLC0415

    if value == "off":
        return CaptionsOff()
    if "/" in value:
        provider, model = parse_external_llm(value)
        return CaptionsLlm(provider, model)
    if value in LOCAL_PRESETS:
        return CaptionsLocal(value)
    presets = ", ".join(LOCAL_PRESETS)
    console.print(
        f"[red]Invalid --captions value '{value}'. Expected: off, a local preset ({presets}), "
        f"or a 'provider/model' slug.[/red]"
    )
    raise typer.Exit(1)


def resolve_captions(value: str | None, llm: str | None, settings: Settings) -> CaptionsSpec:
    """Resolve the captions spec, applying defaults.

    Precedence:
        1. Explicit --captions value (any form).
        2. --llm <provider/model> → captions go to that same model.
        3. First cloud preference in AUTO_CAPTIONS_PREFERENCES with creds in env.
           Today that means ibm/claude-haiku-4-5 if IBM ICA is configured,
           otherwise google/gemini-3.1-flash-lite-preview if GOOGLE_API_KEY is set.
        4. Local default preset (smolvlm).
    """
    from doc_convert.providers import parse_external_llm  # noqa: PLC0415

    if value is not None:
        return parse_captions(value)
    if llm:
        provider, model = parse_external_llm(llm)
        return CaptionsLlm(provider, model)
    for spec in AUTO_CAPTIONS_PREFERENCES:
        provider, model = parse_external_llm(spec)
        if _provider_has_credentials(provider, settings):
            return CaptionsLlm(provider, model)
    return CaptionsLocal(DEFAULT_LOCAL_PRESET)


def _provider_has_credentials(provider: str, settings: Settings) -> bool:
    if provider == "google":
        return bool(settings.google_api_key)
    if provider == "openrouter":
        return bool(settings.openrouter_api_key)
    if provider == "ibm":
        return bool(settings.ibm_ica_model_key and settings.ibm_ica_base_url)
    return False
