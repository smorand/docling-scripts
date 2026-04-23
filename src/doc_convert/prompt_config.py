"""Prompt configuration loader.

Loads prompts from ~/.config/doc-convert/config.yaml if it exists,
falling back to hardcoded defaults.

To customize prompts, copy the default config to ~/.config/doc-convert/config.yaml
and edit the desired prompts. Missing keys fall back to defaults.

Usage:
    from doc_convert.prompt_config import get_prompt

    prompt = get_prompt("audio", "transcription_system_prompt", DEFAULT_VALUE)
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path.home() / ".config" / "doc-convert" / "config.yaml"

_config: dict | None = None


def _load_config() -> dict:
    """Load config from YAML file, cached after first load."""
    global _config  # noqa: PLW0603
    if _config is not None:
        return _config

    if CONFIG_PATH.exists():
        import yaml  # noqa: PLC0415

        try:
            _config = yaml.safe_load(CONFIG_PATH.read_text()) or {}
            logger.debug("Loaded config from %s", CONFIG_PATH)
        except Exception:
            logger.warning("Failed to parse %s, using defaults", CONFIG_PATH)
            _config = {}
    else:
        _config = {}

    return _config


def get_prompt(section: str, key: str, default: str) -> str:
    """Get a prompt from config, falling back to the hardcoded default.

    Args:
        section: Config section (e.g. "audio", "video", "analysis")
        key: Prompt key within the section (e.g. "transcription_system_prompt")
        default: Hardcoded default value if not found in config
    """
    config = _load_config()
    value = config.get(section, {}).get(key)
    if value is not None:
        return str(value)
    return default
