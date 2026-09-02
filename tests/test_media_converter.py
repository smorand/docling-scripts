"""Tests for MediaConverter parallel chunk transcription."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from audio_prep import AudioPart
from doc_convert.base import ConvertOptions
from doc_convert.converters.media import MediaConverter


def test_transcribe_audio_parts_parallel_preserves_order(tmp_path: Path) -> None:
    source = tmp_path / "recording.ogg"
    source.write_bytes(b"dummy")
    out_dir = tmp_path / "out"

    options = ConvertOptions(output_dir=out_dir, llm_concurrency=4)
    converter = MediaConverter(source, options, media_type="audio")

    parts = [
        AudioPart(path=tmp_path / "part_01.ogg", index=0, start_s=0.0, end_s=600.0),
        AudioPart(path=tmp_path / "part_02.ogg", index=1, start_s=540.0, end_s=1200.0),
    ]
    for p in parts:
        p.path.write_bytes(b"dummy_part")

    def _mock_process_media(path: Path, *args: object, **kwargs: object) -> str:
        if "part_01" in path.name:
            return "Transcript Part 1 content"
        return "Transcript Part 2 content"

    with mock.patch("media_llm.process_media", side_effect=_mock_process_media):
        res = converter._transcribe_audio_parts(
            parts, "ibm", "gemini-3.7-flash", "test-key", "http://test", "prompt", "system"
        )

    assert "## Part 1 of 2 (00:00:00 to 00:10:00)" in res
    assert "Transcript Part 1 content" in res
    assert "## Part 2 of 2 (00:09:00 to 00:20:00)" in res
    assert "Transcript Part 2 content" in res
    # Verify order
    p1_idx = res.index("Transcript Part 1 content")
    p2_idx = res.index("Transcript Part 2 content")
    assert p1_idx < p2_idx


def test_transcribe_audio_parts_concurrency_one(tmp_path: Path) -> None:
    source = tmp_path / "recording.ogg"
    source.write_bytes(b"dummy")
    out_dir = tmp_path / "out"

    options = ConvertOptions(output_dir=out_dir, llm_concurrency=1)
    converter = MediaConverter(source, options, media_type="audio")

    parts = [
        AudioPart(path=tmp_path / "part_01.ogg", index=0, start_s=0.0, end_s=600.0),
    ]
    parts[0].path.write_bytes(b"dummy_part")

    with mock.patch("media_llm.process_media", return_value="Single part content"):
        res = converter._transcribe_audio_parts(
            parts, "ibm", "gemini-3.7-flash", "test-key", "http://test", "prompt", "system"
        )

    assert "## Part 1 of 1 (00:00:00 to 00:10:00)" in res
    assert "Single part content" in res
