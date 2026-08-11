"""Tests for the --llm-concurrency flag and its deprecated alias."""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from doc_convert import cli
from doc_convert.providers import DEFAULT_LLM_CONCURRENCY

runner = CliRunner()


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercept _dispatch so we see the resolved options without converting."""
    seen: dict[str, Any] = {}

    def spy(**kwargs: Any) -> None:
        seen.update(kwargs)
        raise SystemExit(0)

    monkeypatch.setattr(cli, "_dispatch", spy)
    return seen


def test_default_is_the_measured_knee(captured: dict[str, Any]) -> None:
    """8 is not arbitrary: it is the last worker count measured to scale cleanly
    (7.7x overlap on a 52-slide deck with per-call latency still ~20 s, while 12
    inflates it to 27.5 s). See the table in providers.py."""
    runner.invoke(cli.app, ["deck.pptx"])
    assert captured["llm_concurrency"] == DEFAULT_LLM_CONCURRENCY == 8


def test_canonical_flag_is_honoured(captured: dict[str, Any]) -> None:
    runner.invoke(cli.app, ["deck.pptx", "--llm-concurrency", "5"])
    assert captured["llm_concurrency"] == 5


def test_deprecated_alias_still_works_and_warns(captured: dict[str, Any]) -> None:
    """--slide-concurrency shipped before captions shared the same pool; it must
    keep working rather than silently doing nothing."""
    result = runner.invoke(cli.app, ["deck.pptx", "--slide-concurrency", "3"])
    assert captured["llm_concurrency"] == 3
    assert "deprecated" in result.output


def test_canonical_flag_wins_over_the_alias(captured: dict[str, Any]) -> None:
    """If someone passes both, the supported flag decides."""
    runner.invoke(cli.app, ["deck.pptx", "--llm-concurrency", "6", "--slide-concurrency", "2"])
    assert captured["llm_concurrency"] == 6


def test_zero_is_rejected() -> None:
    """A pool of 0 workers would hang; Typer must reject it at parse time."""
    result = runner.invoke(cli.app, ["deck.pptx", "--llm-concurrency", "0"])
    assert result.exit_code != 0
