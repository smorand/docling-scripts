"""Tests for media MIME helpers."""

from __future__ import annotations

from pathlib import Path

from media_llm import (
    get_image_mime,
    get_media_mime,
    is_audio_ext,
    is_image_ext,
    is_video_ext,
)


def test_is_image_ext() -> None:
    assert is_image_ext(".png")
    assert is_image_ext(".JPEG")
    assert not is_image_ext(".pdf")


def test_get_image_mime() -> None:
    assert get_image_mime(Path("a.png")) == "image/png"
    assert get_image_mime(Path("a.jpg")) == "image/jpeg"
    # unknown image extension falls back to png
    assert get_image_mime(Path("a.heic")) == "image/png"


def test_is_audio_ext() -> None:
    assert is_audio_ext(".ogg")
    assert is_audio_ext(".MP3")
    assert not is_audio_ext(".mp4")


def test_is_video_ext() -> None:
    assert is_video_ext(".mp4")
    assert is_video_ext(".MKV")
    assert not is_video_ext(".ogg")


def test_get_media_mime() -> None:
    assert get_media_mime(Path("a.ogg")) == "audio/ogg"
    assert get_media_mime(Path("a.mp4")) == "video/mp4"
    assert get_media_mime(Path("a.bin")) == "application/octet-stream"
