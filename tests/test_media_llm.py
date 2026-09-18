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


# ---------------------------------------------------------------------------
# _gemini_generate: blockReason retry logic
# ---------------------------------------------------------------------------

_GEMINI_GENERATE_URL = "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"
_GOOD_CANDIDATE = {"candidates": [{"content": {"parts": [{"text": "hello world"}]}, "finishReason": "STOP"}]}
_EMPTY_OTHER = {"promptFeedback": {"blockReason": "OTHER"}, "candidates": []}
_EMPTY_SAFETY = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
_EMPTY_RECITATION = {"promptFeedback": {"blockReason": "RECITATION"}, "candidates": []}
_EMPTY_NO_FEEDBACK = {"candidates": []}  # missing promptFeedback entirely


class _FakeHttpxClient:
    """Minimal httpx.Client stand-in for _gemini_generate tests."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def __enter__(self) -> _FakeHttpxClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def post(self, _url: str, **_kwargs: object) -> _FakeHttpxResp:
        self.calls += 1
        resp = self._responses.pop(0) if self._responses else _FakeHttpxResp(200, _GOOD_CANDIDATE)
        if isinstance(resp, Exception):
            raise resp
        return resp


class _FakeHttpxResp:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,
                response=None,  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return self._body


def _patch_gemini_client(monkeypatch: pytest.MonkeyPatch, client: _FakeHttpxClient) -> None:
    """Replace httpx.Client used inside _gemini_generate."""

    class _Ctx:
        def __init__(self, **_kw: object) -> None:
            pass

        def __enter__(self) -> _FakeHttpxClient:
            return client

        def __exit__(self, *_: object) -> None:
            pass

    monkeypatch.setattr(media_llm.httpx, "Client", _Ctx)


def test_gemini_generate_retries_block_reason_other_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """blockReason=OTHER is transient; the second attempt must succeed."""

    client = _FakeHttpxClient(
        [
            _FakeHttpxResp(200, _EMPTY_OTHER),
            _FakeHttpxResp(200, _GOOD_CANDIDATE),
        ]
    )
    _patch_gemini_client(monkeypatch, client)
    monkeypatch.setattr(media_llm.time, "sleep", lambda _: None)

    result = media_llm._gemini_generate(  # type: ignore[attr-defined]
        file_uri="files/abc",
        mime_type="audio/ogg",
        model="test-model",
        prompt="transcribe",
        api_key="key",
        extra_files=None,
        system_prompt=None,
    )
    assert result == "hello world"
    assert client.calls == 2


def test_gemini_generate_retries_missing_feedback_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty candidates with no promptFeedback key (reason=unknown) must also be retried."""

    client = _FakeHttpxClient(
        [
            _FakeHttpxResp(200, _EMPTY_NO_FEEDBACK),
            _FakeHttpxResp(200, _GOOD_CANDIDATE),
        ]
    )
    _patch_gemini_client(monkeypatch, client)
    monkeypatch.setattr(media_llm.time, "sleep", lambda _: None)

    result = media_llm._gemini_generate(  # type: ignore[attr-defined]
        file_uri="files/abc",
        mime_type="audio/ogg",
        model="test-model",
        prompt="transcribe",
        api_key="key",
        extra_files=None,
        system_prompt=None,
    )
    assert result == "hello world"
    assert client.calls == 2


def test_gemini_generate_raises_immediately_on_safety_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """blockReason=SAFETY is a policy refusal; must raise on first attempt without retry."""

    client = _FakeHttpxClient([_FakeHttpxResp(200, _EMPTY_SAFETY)])
    _patch_gemini_client(monkeypatch, client)
    monkeypatch.setattr(media_llm.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="SAFETY"):
        media_llm._gemini_generate(  # type: ignore[attr-defined]
            file_uri="files/abc",
            mime_type="audio/ogg",
            model="test-model",
            prompt="transcribe",
            api_key="key",
            extra_files=None,
            system_prompt=None,
        )
    assert client.calls == 1  # no retry on hard block


def test_gemini_generate_raises_immediately_on_recitation_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """blockReason=RECITATION must raise on first attempt."""

    client = _FakeHttpxClient([_FakeHttpxResp(200, _EMPTY_RECITATION)])
    _patch_gemini_client(monkeypatch, client)
    monkeypatch.setattr(media_llm.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match="RECITATION"):
        media_llm._gemini_generate(  # type: ignore[attr-defined]
            file_uri="files/abc",
            mime_type="audio/ogg",
            model="test-model",
            prompt="transcribe",
            api_key="key",
            extra_files=None,
            system_prompt=None,
        )
    assert client.calls == 1


def test_gemini_generate_exhausts_retries_on_persistent_other_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent OTHER block after all attempts raises when fallback is already attempted."""

    max_attempts = media_llm._GEMINI_MAX_ATTEMPTS  # type: ignore[attr-defined]
    client = _FakeHttpxClient([_FakeHttpxResp(200, _EMPTY_OTHER)] * max_attempts)
    _patch_gemini_client(monkeypatch, client)
    monkeypatch.setattr(media_llm.time, "sleep", lambda _: None)

    with pytest.raises(RuntimeError, match=str(max_attempts)):
        media_llm._gemini_generate(  # type: ignore[attr-defined]
            file_uri="files/abc",
            mime_type="audio/ogg",
            model="test-model",
            prompt="transcribe",
            api_key="key",
            extra_files=None,
            system_prompt=None,
            _fallback_attempted=True,  # bypass fallback to test exhaustion path
        )
    assert client.calls == max_attempts


def test_gemini_generate_falls_back_to_alternative_model_on_persistent_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """OTHER block exhausting all retries on original model must retry on the fallback model and succeed."""
    max_attempts = media_llm._GEMINI_MAX_ATTEMPTS  # type: ignore[attr-defined]

    # First max_attempts calls block with OTHER (original model), then one succeeds (fallback model).
    responses = [_FakeHttpxResp(200, _EMPTY_OTHER)] * max_attempts + [_FakeHttpxResp(200, _GOOD_CANDIDATE)]
    client = _FakeHttpxClient(responses)
    _patch_gemini_client(monkeypatch, client)
    monkeypatch.setattr(media_llm.time, "sleep", lambda _: None)

    result = media_llm._gemini_generate(  # type: ignore[attr-defined]
        file_uri="files/abc",
        mime_type="audio/ogg",
        model="gemini-3.7-flash",  # original model, different from fallback
        prompt="transcribe",
        api_key="key",
        extra_files=None,
        system_prompt=None,
    )
    assert result == "hello world"
    assert client.calls == max_attempts + 1  # max_attempts blocks + 1 fallback success
