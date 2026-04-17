"""Format detection and VLM preset definitions."""

from __future__ import annotations

from enum import Enum

import typer
from docling.datamodel.base_models import InputFormat

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


def detect_format(path: Path) -> InputFormat:  # noqa: F821
    """Detect input format from file extension."""
    ext = path.suffix.lower()
    fmt = EXT_TO_FORMAT.get(ext)
    if fmt is None:
        console.print(f"[red]Unsupported extension '{ext}'[/red]")
        console.print(f"Supported: {', '.join(sorted(EXT_TO_FORMAT.keys()))}")
        raise typer.Exit(1)
    return fmt


class VlmPreset(str, Enum):
    smolvlm = "smolvlm"
    granite_vision = "granite_vision"
    pixtral = "pixtral"
    qwen = "qwen"


PRESET_REPO_IDS: dict[str, str] = {
    "smolvlm": "HuggingFaceTB/SmolVLM-256M-Instruct",
    "granite_vision": "ibm-granite/granite-vision-3.3-2b",
    "pixtral": "mistral-community/pixtral-12b",
    "qwen": "Qwen/Qwen2.5-VL-3B-Instruct",
}
