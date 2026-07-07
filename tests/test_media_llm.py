"""Tests for media MIME helpers and the inline-provider retry logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

import media_llm
from media_llm import (
    _MAX_MEDIA_ATTEMPTS,
    _send_media_request,
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
    assert is_audio_ext(".opus")
    assert is_audio_ext(".OPUS")
    assert not is_audio_ext(".mp4")


def test_is_video_ext() -> None:
    assert is_video_ext(".mp4")
    assert is_video_ext(".MKV")
    assert not is_video_ext(".ogg")


def test_get_media_mime() -> None:
    assert get_media_mime(Path("a.ogg")) == "audio/ogg"
    assert get_media_mime(Path("a.mp4")) == "video/mp4"
    assert get_media_mime(Path("a.bin")) == "application/octet-stream"


# ── Inline-provider retry / 502 disambiguation ───────────────────────────────


class _FakeResp:
    """Minimal stand-in for httpx.Response for the retry helper."""

    def __init__(self, status_code: int, *, json_data: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if not self.is_success:
            msg = f"status {self.status_code}"
            raise RuntimeError(msg)


class _FakeClient:
    """Returns queued responses in order (last repeats if exhausted).

    A queued item that is an ``Exception`` instance is raised instead of
    returned, to simulate httpx transport/timeout failures.
    """

    def __init__(self, responses: list[_FakeResp | Exception]) -> None:
        self._responses = responses
        self.calls = 0

    def post(self, url: str, headers: dict[str, str] | None = None, json: Any = None) -> _FakeResp:
        item = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never actually sleep during retry-backoff tests."""
    monkeypatch.setattr(media_llm.time, "sleep", lambda _s: None)


def _send(client: _FakeClient, *, raw_size_mb: float = 4.6) -> Any:
    return _send_media_request(
        client,  # type: ignore[arg-type]
        "https://example/ica",
        {"Authorization": "Bearer x"},
        {"model": "m", "messages": []},
        provider_label="ibm",
        file_name="part_02.ogg",
        raw_size_mb=raw_size_mb,
    )


def test_retries_transient_502_then_succeeds() -> None:
    # The reported bug: a small part 502s once, then transcribes fine on retry.
    client = _FakeClient([_FakeResp(502), _FakeResp(200, json_data={"ok": True})])
    resp = _send(client)
    assert resp.status_code == 200
    assert client.calls == 2


def test_retries_read_timeout_then_succeeds() -> None:
    # The ReadTimeout bug: IBM's gateway hangs, httpx raises (no HTTP status);
    # this must be retried like any transient, not crash the whole conversion.

    client = _FakeClient([httpx.ReadTimeout("timed out"), _FakeResp(200, json_data={"ok": True})])
    resp = _send(client)
    assert resp.status_code == 200
    assert client.calls == 2


def test_persistent_read_timeout_reports_gateway_timeout() -> None:

    client = _FakeClient([httpx.ReadTimeout("timed out")])
    with pytest.raises(RuntimeError, match="timed out"):
        _send(client)
    assert client.calls == _MAX_MEDIA_ATTEMPTS


def test_small_502_is_transient_not_too_large() -> None:
    # A 4.6 MB payload 502ing is a flaky gateway, never "too large".
    client = _FakeClient([_FakeResp(502)])
    with pytest.raises(RuntimeError, match="transient gateway error") as exc:
        _send(client, raw_size_mb=4.6)
    assert "too large" not in str(exc.value).lower()
    assert client.calls == _MAX_MEDIA_ATTEMPTS  # retried to exhaustion


def test_large_502_is_too_large_and_does_not_retry() -> None:
    client = _FakeClient([_FakeResp(502)])
    with pytest.raises(RuntimeError, match="too large"):
        _send(client, raw_size_mb=100.0)
    assert client.calls == 1  # genuine oversize: no retry


def test_413_is_too_large_and_does_not_retry() -> None:
    client = _FakeClient([_FakeResp(413)])
    with pytest.raises(RuntimeError, match="too large"):
        _send(client, raw_size_mb=4.6)
    assert client.calls == 1


def test_gateway_timeout_retries_then_reports_timeout() -> None:
    client = _FakeClient([_FakeResp(524)])
    with pytest.raises(RuntimeError, match="timed out"):
        _send(client)
    assert client.calls == _MAX_MEDIA_ATTEMPTS


def test_success_first_try_makes_one_call() -> None:
    client = _FakeClient([_FakeResp(200, json_data={"ok": True})])
    resp = _send(client)
    assert resp.status_code == 200
    assert client.calls == 1
