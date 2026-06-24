"""Tests for video helpers: YouTube detection, timestamps, prompt builders."""

from __future__ import annotations

from video import (
    build_analysis_prompt,
    build_extraction_prompt,
    format_timestamp,
    get_meta_summary_prompt,
    is_youtube_url,
)


def test_is_youtube_url_variants() -> None:
    assert is_youtube_url("https://www.youtube.com/watch?v=-QFHIoCo-Ko")
    assert is_youtube_url("https://youtu.be/abc123DEF45")
    assert is_youtube_url("youtube.com/shorts/xyz789ABCde")


def test_is_youtube_url_negative() -> None:
    assert not is_youtube_url("https://vimeo.com/12345")
    assert not is_youtube_url("/tmp/local.mp4")


def test_format_timestamp() -> None:
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(65) == "00:01:05"
    assert format_timestamp(3661) == "01:01:01"


def test_build_extraction_prompt_plain() -> None:
    prompt, system = build_extraction_prompt()
    assert "video" in prompt.lower()
    assert system is not None
    assert "## Transcription" in system


def test_build_extraction_prompt_with_context() -> None:
    prompt, system = build_extraction_prompt("Quarterly Review")
    assert "Quarterly Review" in prompt
    assert system is not None
    assert "Quarterly Review" in system


def test_build_analysis_prompt_with_context() -> None:
    prompt, system = build_analysis_prompt("Demo Day")
    assert "Demo Day" in prompt
    assert system is not None
    assert "Demo Day" in system


def test_get_meta_summary_prompt() -> None:
    assert "Executive Summary" in get_meta_summary_prompt()
