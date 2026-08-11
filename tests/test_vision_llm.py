"""Tests for the shared vision-LLM primitive (transport, retry, fanout).

No network: the httpx client is faked. These tests pin the contract every caller
depends on, in particular that a failure degrades to "no description" instead of
aborting a document, and that a wrong model slug fails fast.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pytest
import typer
from PIL import Image

from config import Settings
from doc_convert.providers import RETRYABLE_HTTP_STATUS
from doc_convert.vision_llm import (
    MAX_ATTEMPTS,
    VisionAttempt,
    build_messages,
    describe_image,
    encode_image,
    map_concurrent,
    request_once,
)


def _png(path: Path, size: tuple[int, int] = (40, 30)) -> Path:
    Image.new("RGB", size, (10, 90, 180)).save(path, format="PNG")
    return path


class _FakeResponse:
    def __init__(self, status: int, payload: object = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _FakeClient:
    """Records the payload so tests can assert on the request we actually send."""

    def __init__(self, response: object = None, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise = raise_exc
        self.calls: list[dict] = []

    def post(self, _url: str, **kwargs: object) -> object:
        self.calls.append(kwargs.get("json"))  # type: ignore[arg-type]
        if self._raise is not None:
            raise self._raise
        return self._response


def _ok(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


# ---------------------------------------------------------------------------
# Message shape: switching transports must not change what the model receives
# ---------------------------------------------------------------------------


def test_caption_messages_put_the_image_before_the_text() -> None:
    """Docling's api_image_request sent [image, text] in one user message. Keeping
    that ordering is what makes the transport swap a no-op for caption wording."""
    msgs = build_messages("describe this", "image/png", "BASE64")
    assert len(msgs) == 1
    assert msgs[0]["role"] == "user"
    kinds = [part["type"] for part in msgs[0]["content"]]
    assert kinds == ["image_url", "text"]
    assert msgs[0]["content"][0]["image_url"]["url"] == "data:image/png;base64,BASE64"
    assert msgs[0]["content"][1]["text"] == "describe this"


def test_system_prompt_uses_system_plus_text_then_image() -> None:
    """The slide pass shape, validated on 98 slides, is preserved as-is."""
    msgs = build_messages("analyze it", "image/jpeg", "B64", system="you are a slide reader")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == "you are a slide reader"
    assert [part["type"] for part in msgs[1]["content"]] == ["text", "image_url"]


def test_request_sends_temperature_zero_and_max_tokens(tmp_path: Path, ibm_settings: Settings) -> None:
    """Captions must stay reproducible; docling used temperature 0.0."""
    client = _FakeClient(_FakeResponse(200, _ok("desc")))
    request_once(build_messages("p", "image/png", "b"), "ibm", "m", ibm_settings, "key", client)  # type: ignore[arg-type]
    payload = client.calls[0]
    assert payload["temperature"] == 0.0
    assert payload["max_tokens"] == ibm_settings.llm_max_tokens
    assert payload["model"] == "m"


def test_encode_image_reports_mime_from_the_prepared_file(tmp_path: Path) -> None:
    mime, b64 = encode_image(_png(tmp_path / "a.png"))
    assert mime == "image/png"
    assert len(b64) > 0


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(RETRYABLE_HTTP_STATUS))
def test_transient_status_is_retryable(status: int, ibm_settings: Settings) -> None:
    out = request_once(
        build_messages("p", "image/png", "b"), "ibm", "m", ibm_settings, "key", _FakeClient(_FakeResponse(status))
    )  # type: ignore[arg-type]
    assert out.retryable is True
    assert out.text == ""


def test_transport_error_is_retryable(ibm_settings: Settings) -> None:
    """A read timeout under concurrency must be retried, not dropped."""
    client = _FakeClient(raise_exc=httpx.ReadTimeout("too slow"))
    out = request_once(build_messages("p", "image/png", "b"), "ibm", "m", ibm_settings, "key", client)  # type: ignore[arg-type]
    assert out.retryable is True
    assert "ReadTimeout" in out.reason


def test_unknown_model_aborts_hard(ibm_settings: Settings) -> None:
    """404 means the slug is wrong: retrying it on every image is pure waste."""
    with pytest.raises(typer.Exit):
        request_once(
            build_messages("p", "image/png", "b"), "ibm", "nope", ibm_settings, "key", _FakeClient(_FakeResponse(404))
        )  # type: ignore[arg-type]


def test_client_error_is_not_retryable(ibm_settings: Settings) -> None:
    out = request_once(
        build_messages("p", "image/png", "b"),
        "ibm",
        "m",
        ibm_settings,
        "key",
        _FakeClient(_FakeResponse(400, text="bad")),
    )  # type: ignore[arg-type]
    assert out.retryable is False


def test_empty_completion_is_retryable(ibm_settings: Settings) -> None:
    client = _FakeClient(_FakeResponse(200, _ok("   \n ")))
    out = request_once(build_messages("p", "image/png", "b"), "ibm", "m", ibm_settings, "key", client)  # type: ignore[arg-type]
    assert out.retryable is True


def test_malformed_body_is_not_retryable(ibm_settings: Settings) -> None:
    client = _FakeClient(_FakeResponse(200, {"unexpected": "shape"}))
    out = request_once(build_messages("p", "image/png", "b"), "ibm", "m", ibm_settings, "key", client)  # type: ignore[arg-type]
    assert out.retryable is False
    assert out.text == ""


def test_successful_response_returns_text(ibm_settings: Settings) -> None:
    client = _FakeClient(_FakeResponse(200, _ok("a bar chart of revenue")))
    out = request_once(build_messages("p", "image/png", "b"), "ibm", "m", ibm_settings, "key", client)  # type: ignore[arg-type]
    assert out.text == "a bar chart of revenue"
    assert out.retryable is False


# ---------------------------------------------------------------------------
# Retry loop
# ---------------------------------------------------------------------------


def test_retry_recovers_from_transient_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings
) -> None:
    attempts = {"n": 0}

    def fake_request(*_args: object, **_kwargs: object) -> VisionAttempt:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return VisionAttempt(retryable=True, reason="HTTP 429")
        return VisionAttempt(text="recovered")

    monkeypatch.setattr("doc_convert.vision_llm.request_once", fake_request)
    out = describe_image(
        _png(tmp_path / "a.png"), "p", "ibm", "m", ibm_settings, "key", object(), sleep=lambda _s: None
    )  # type: ignore[arg-type]
    assert out == "recovered"
    assert attempts["n"] == 3


def test_retry_gives_up_after_max_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings
) -> None:
    calls = {"n": 0}

    def always_transient(*_args: object, **_kwargs: object) -> VisionAttempt:
        calls["n"] += 1
        return VisionAttempt(retryable=True, reason="HTTP 503")

    monkeypatch.setattr("doc_convert.vision_llm.request_once", always_transient)
    out = describe_image(
        _png(tmp_path / "a.png"), "p", "ibm", "m", ibm_settings, "key", object(), sleep=lambda _s: None
    )  # type: ignore[arg-type]
    assert out == ""
    assert calls["n"] == MAX_ATTEMPTS


def test_non_retryable_failure_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings
) -> None:
    """A 400 will not fix itself; burning the backoff schedule on it is waste."""
    calls = {"n": 0}

    def hard_failure(*_args: object, **_kwargs: object) -> VisionAttempt:
        calls["n"] += 1
        return VisionAttempt(reason="HTTP 400")

    monkeypatch.setattr("doc_convert.vision_llm.request_once", hard_failure)
    out = describe_image(
        _png(tmp_path / "a.png"), "p", "ibm", "m", ibm_settings, "key", object(), sleep=lambda _s: None
    )  # type: ignore[arg-type]
    assert out == ""
    assert calls["n"] == 1


def test_unreadable_image_returns_empty_without_calling_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ibm_settings: Settings
) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")

    def boom(*_args: object, **_kwargs: object) -> VisionAttempt:
        raise AssertionError("the API must not be called for an unreadable image")

    monkeypatch.setattr("doc_convert.vision_llm.request_once", boom)
    assert describe_image(broken, "p", "ibm", "m", ibm_settings, "key", object()) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Concurrent fanout
# ---------------------------------------------------------------------------


def test_map_concurrent_preserves_input_order() -> None:
    """Completion order is nondeterministic; results must follow the input."""
    items = list(range(20))

    def work(i: int) -> str:
        time.sleep((20 - i) * 0.002)  # later items finish first
        return f"r{i}"

    assert map_concurrent(items, work, 4) == [f"r{i}" for i in items]


def test_map_concurrent_actually_overlaps_and_respects_the_cap() -> None:
    peak = 0
    live = 0
    lock = threading.Lock()

    def work(_i: int) -> int:
        nonlocal peak, live
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return _i

    map_concurrent(list(range(12)), work, 4)
    assert peak > 1, "requests must actually overlap"
    assert peak <= 4, "must never exceed the requested concurrency"


def test_map_concurrent_one_is_sequential() -> None:
    peak = 0
    live = 0
    lock = threading.Lock()

    def work(_i: int) -> int:
        nonlocal peak, live
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.02)
        with lock:
            live -= 1
        return _i

    map_concurrent(list(range(5)), work, 1)
    assert peak == 1


def test_map_concurrent_empty_input() -> None:
    assert map_concurrent([], lambda x: x, 4) == []


def test_map_concurrent_propagates_fatal_errors() -> None:
    """typer.Exit from a bad model slug must surface, not be swallowed per item."""

    def work(i: int) -> int:
        raise typer.Exit(1)

    with pytest.raises(typer.Exit):
        map_concurrent(list(range(8)), work, 4)


def test_map_concurrent_reports_progress_in_completion_order() -> None:
    seen: list[tuple[int, int]] = []

    def work(i: int) -> int:
        return i * 10

    def on_done(done: int, item: int, value: int, _elapsed: float) -> None:
        seen.append((done, item))
        assert value == item * 10

    map_concurrent([1, 2, 3], work, 2, on_done=on_done)
    assert sorted(d for d, _ in seen) == [1, 2, 3]
    assert sorted(i for _, i in seen) == [1, 2, 3]
