"""Multi-file batch orchestration.

When ``doc-convert`` receives more than one input, each file is converted in its
own ``doc-convert`` subprocess. Subprocess isolation is deliberate: every child
gets its own output guard, signal handlers, tracing span, and model load, so a
failure on one file never aborts the batch and there is no shared torch/MPS or
global-state contention (see ``output_guard`` and the PDF pipeline).

Concurrency is bounded by ``-P/--parallel``:

* ``1`` (default): sequential, child output streamed live (same feel as a
  single-file run, prefixed with ``[i/N] name``).
* ``>1`` or ``0`` (all cores): child output is captured and printed as a block
  when each file finishes, followed by an ordered summary table.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404 - we re-invoke our own console entry point with a fixed argv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from logging_config import console

_PARALLEL_FLAGS = ("-P", "--parallel")


@dataclass
class _Result:
    document: str
    returncode: int
    output: str


def _resolve_workers(parallel: int, count: int) -> int:
    """Turn the ``--parallel`` value into an actual worker count.

    ``0`` means one worker per CPU core. The result is clamped to ``count`` so we
    never spawn more workers than there are files.
    """
    workers = (os.cpu_count() or 1) if parallel <= 0 else parallel
    return max(1, min(workers, count))


def _child_command(documents: list[str], document: str) -> list[str]:
    """Build the child ``doc-convert`` command for a single ``document``.

    Reuses this process's own flags (``sys.argv[1:]``) with the file tokens and
    the ``--parallel`` option stripped, then appends the single target file. The
    child sees exactly one positional argument, so it takes the normal
    single-file path (parallelism is irrelevant there).
    """
    file_set = set(documents)
    passthrough: list[str] = []
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _PARALLEL_FLAGS:
            # Skip the flag and its value (normalized to two tokens upstream).
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            i += 2 if (nxt is not None and _is_nonneg_int(nxt)) else 1
            continue
        if tok.startswith("--parallel=") or (tok.startswith("-P") and _is_nonneg_int(tok[2:])):
            i += 1
            continue
        if tok in file_set:
            i += 1
            continue
        passthrough.append(tok)
        i += 1
    return [sys.argv[0], *passthrough, document]


def _is_nonneg_int(value: str) -> bool:
    return value.isdigit()


def _run_one_captured(documents: list[str], document: str) -> _Result:
    """Convert one file in a child process, capturing all of its output."""
    cmd = _child_command(documents, document)
    proc = subprocess.run(  # nosec B603 - fixed argv, no shell, our own entry point
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    return _Result(document=document, returncode=proc.returncode, output=(proc.stdout or "") + (proc.stderr or ""))


def _run_one_live(documents: list[str], document: str) -> _Result:
    """Convert one file in a child process, streaming its output to the terminal."""
    cmd = _child_command(documents, document)
    proc = subprocess.run(cmd, check=False)  # nosec B603 - fixed argv, no shell, our own entry point
    return _Result(document=document, returncode=proc.returncode, output="")


def _print_summary(results: list[_Result]) -> None:
    ok = [r for r in results if r.returncode == 0]
    failed = [r for r in results if r.returncode != 0]
    console.print()
    console.print(f"[bold]Batch complete:[/bold] {len(ok)} ok, {len(failed)} failed (of {len(results)})")
    for r in failed:
        console.print(f"  [red]FAILED[/red] {Path(r.document).name} (exit {r.returncode})")


def run_batch(documents: list[str], parallel: int) -> int:
    """Convert every input in ``documents``; return a process exit code.

    Exit code is ``0`` only when every file converted successfully, else ``1``.
    Interrupting (Ctrl+C) propagates SIGINT to the child process group, so each
    child cleans up its own partial output before the batch reports and exits.
    """
    workers = _resolve_workers(parallel, len(documents))
    results: list[_Result] = []

    try:
        if workers == 1:
            for idx, document in enumerate(documents, start=1):
                console.print(f"[bold cyan][{idx}/{len(documents)}] {Path(document).name}[/bold cyan]")
                results.append(_run_one_live(documents, document))
        else:
            console.print(f"[dim]Processing {len(documents)} files, {workers} at a time[/dim]")
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_run_one_captured, documents, doc): doc for doc in documents}
                for future in as_completed(futures):
                    result = future.result()
                    status = "[green]OK[/green]" if result.returncode == 0 else "[red]FAILED[/red]"
                    console.print(f"\n[bold]{status} {Path(result.document).name}[/bold]")
                    if result.output.strip():
                        console.print(result.output.rstrip())
                    results.append(result)
    except KeyboardInterrupt:
        console.print("\n[yellow]Batch interrupted; child conversions cleaned up their own outputs[/yellow]")
        return 130

    _print_summary(results)
    return 0 if all(r.returncode == 0 for r in results) else 1


def normalize_parallel_argv(argv: list[str]) -> list[str]:
    """Rewrite a bare ``-P``/``--parallel`` (no value) into ``-P 0`` (all cores).

    Typer cannot express an option whose value is optional, so we normalize the
    argument vector before Typer parses it. A ``-P`` immediately followed by a
    non-negative integer keeps that value; otherwise ``0`` is injected. Glued
    forms (``-P4``, ``--parallel=4``) already carry their value and pass through.
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _PARALLEL_FLAGS:
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and _is_nonneg_int(nxt):
                out.append(tok)
                out.append(nxt)
                i += 2
            else:
                out.append(tok)
                out.append("0")
                i += 1
            continue
        out.append(tok)
        i += 1
    return out


__all__ = ["normalize_parallel_argv", "run_batch"]
