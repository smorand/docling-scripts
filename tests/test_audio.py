"""Tests for audio prompt builders."""

from __future__ import annotations

from audio import build_analysis_prompt, build_transcription_prompt


def test_build_transcription_prompt_plain() -> None:
    prompt, system = build_transcription_prompt()
    assert "Transcribe" in prompt
    assert system is not None and system.strip()


def test_build_transcription_prompt_with_meeting() -> None:
    prompt, system = build_transcription_prompt("Standup")
    assert "Standup" in prompt
    assert system is not None
    assert "Standup" in system


def test_build_analysis_prompt_with_meeting() -> None:
    prompt, system = build_analysis_prompt("Retro")
    assert "Retro" in prompt
    assert system is not None
    assert "Retro" in system
