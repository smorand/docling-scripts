"""Tests for the prompt configuration loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from doc_convert import prompt_config


@pytest.fixture(autouse=True)
def _reset_config_cache() -> None:
    """Clear the module-level config cache around each test."""
    prompt_config._config = None
    yield
    prompt_config._config = None


def test_get_prompt_default_when_no_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(prompt_config, "CONFIG_PATH", tmp_path / "missing.yaml")
    assert prompt_config.get_prompt("audio", "system_prompt", "DEFAULT") == "DEFAULT"


def test_get_prompt_reads_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("audio:\n  system_prompt: CUSTOM\n")
    monkeypatch.setattr(prompt_config, "CONFIG_PATH", cfg)
    assert prompt_config.get_prompt("audio", "system_prompt", "DEFAULT") == "CUSTOM"


def test_get_prompt_missing_key_falls_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("audio:\n  other: value\n")
    monkeypatch.setattr(prompt_config, "CONFIG_PATH", cfg)
    assert prompt_config.get_prompt("audio", "system_prompt", "DEFAULT") == "DEFAULT"


def test_load_config_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("audio:\n  system_prompt: FIRST\n")
    monkeypatch.setattr(prompt_config, "CONFIG_PATH", cfg)
    assert prompt_config.get_prompt("audio", "system_prompt", "D") == "FIRST"
    # Mutating the file does not change the cached result.
    cfg.write_text("audio:\n  system_prompt: SECOND\n")
    assert prompt_config.get_prompt("audio", "system_prompt", "D") == "FIRST"


def test_load_config_invalid_yaml_returns_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    cfg.write_text("audio: [unbalanced\n")
    monkeypatch.setattr(prompt_config, "CONFIG_PATH", cfg)
    assert prompt_config.get_prompt("audio", "system_prompt", "DEFAULT") == "DEFAULT"
