"""Tests for multi-file batch orchestration."""

from __future__ import annotations

from unittest import mock

import pytest
from typer.testing import CliRunner

from doc_convert import batch
from doc_convert.cli import app

runner = CliRunner()


# ── argv normalization (bare -P → -P 0) ──────────────────────────────────
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["a.ogg", "b.ogg"], ["a.ogg", "b.ogg"]),
        (["a.ogg", "-P"], ["a.ogg", "-P", "0"]),
        (["-P", "a.ogg"], ["-P", "0", "a.ogg"]),
        (["-P", "4", "a.ogg"], ["-P", "4", "a.ogg"]),
        (["--parallel", "a.ogg"], ["--parallel", "0", "a.ogg"]),
        (["--parallel", "3"], ["--parallel", "3"]),
        (["--parallel=3", "a.ogg"], ["--parallel=3", "a.ogg"]),
    ],
)
def test_normalize_parallel_argv(argv: list[str], expected: list[str]) -> None:
    assert batch.normalize_parallel_argv(argv) == expected


# ── worker resolution ────────────────────────────────────────────────────
def test_resolve_workers_sequential() -> None:
    assert batch._resolve_workers(1, 5) == 1


def test_resolve_workers_all_cores_capped_to_count() -> None:
    with mock.patch("doc_convert.batch.os.cpu_count", return_value=8):
        assert batch._resolve_workers(0, 3) == 3
        assert batch._resolve_workers(0, 20) == 8


def test_resolve_workers_explicit_capped_to_count() -> None:
    assert batch._resolve_workers(4, 2) == 2
    assert batch._resolve_workers(2, 5) == 2


# ── child command reconstruction ─────────────────────────────────────────
def test_child_command_strips_files_and_parallel() -> None:
    argv = ["/bin/doc-convert", "a.ogg", "b.ogg", "-P", "4", "--analyze", "-vv"]
    with mock.patch.object(batch.sys, "argv", argv):
        cmd = batch._child_command(["a.ogg", "b.ogg"], "a.ogg")
    assert cmd == ["/bin/doc-convert", "--analyze", "-vv", "a.ogg"]


def test_child_command_strips_glued_parallel_and_long_value() -> None:
    argv = ["/bin/doc-convert", "x.pdf", "y.pdf", "-P4", "--llm", "ibm/x"]
    with mock.patch.object(batch.sys, "argv", argv):
        cmd = batch._child_command(["x.pdf", "y.pdf"], "y.pdf")
    assert cmd == ["/bin/doc-convert", "--llm", "ibm/x", "y.pdf"]


# ── orchestration (subprocess mocked) ────────────────────────────────────
def _fake_proc(returncode: int, stdout: str = "", stderr: str = "") -> mock.Mock:
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


def test_run_batch_sequential_all_ok() -> None:
    with mock.patch("doc_convert.batch.subprocess.run", return_value=_fake_proc(0)) as run:
        code = batch.run_batch(["a.xlsx", "b.xlsx"], parallel=1)
    assert code == 0
    assert run.call_count == 2


def test_run_batch_parallel_reports_failure() -> None:
    def side_effect(cmd: list[str], **_kwargs: object) -> mock.Mock:
        return _fake_proc(0 if cmd[-1] == "ok.pdf" else 1, stdout="done")

    with mock.patch("doc_convert.batch.subprocess.run", side_effect=side_effect):
        code = batch.run_batch(["ok.pdf", "bad.pdf"], parallel=2)
    assert code == 1


def test_run_batch_interrupt_returns_130() -> None:
    with mock.patch("doc_convert.batch.subprocess.run", side_effect=KeyboardInterrupt):
        code = batch.run_batch(["a.pdf", "b.pdf"], parallel=1)
    assert code == 130


# ── CLI guards for incompatible multi-file combos ────────────────────────
@pytest.mark.parametrize(
    "extra",
    [
        ["-o", "out"],
        ["--start-audio"],
        ["--download-models"],
        ["--download-enrichments"],
    ],
)
def test_multi_file_rejects_incompatible_flags(extra: list[str]) -> None:
    result = runner.invoke(app, ["a.pdf", "b.pdf", *extra])
    assert result.exit_code == 1


def test_multi_file_dispatches_to_batch() -> None:
    with mock.patch("doc_convert.batch.run_batch", return_value=0) as run_batch:
        result = runner.invoke(app, ["a.pdf", "b.pdf"])
    assert result.exit_code == 0
    run_batch.assert_called_once()
    assert run_batch.call_args.args[0] == ["a.pdf", "b.pdf"]
