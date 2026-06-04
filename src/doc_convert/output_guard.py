"""Output directory guard: clean up freshly-created empty <name>_docling/ on failure.

Tracks every output directory a run creates and, on exit (success or failure,
including SIGINT/SIGTERM/SIGHUP/SIGQUIT), removes a directory we created if it
contains no committed artifact. This avoids leaving behind half-empty
``<name>_docling/`` folders that suggest a conversion succeeded when it didn't.

SIGKILL (kill -9) cannot be intercepted; that is a hard OS limit.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path  # noqa: TC003  — used as runtime type for dataclass field

from logging_config import console

logger = logging.getLogger(__name__)

# Files that, if present in an output directory, mean we should keep it.
_KEEP_MARKERS: tuple[str, ...] = ("document.md", "audio.ogg")


@dataclass
class _Guard:
    out_dir: Path
    preexisted: bool
    preexisting_entries: set[str] = field(default_factory=set)


_guards: list[_Guard] = []
_handlers_installed: bool = False


def register(out_dir: Path) -> None:
    """Track ``out_dir`` so it can be cleaned up if the run fails.

    Safe to call before or after the directory exists.
    """
    preexisted = out_dir.exists()
    preexisting_entries: set[str] = set()
    if preexisted:
        preexisting_entries = {p.name for p in out_dir.iterdir()}
    _guards.append(_Guard(out_dir=out_dir, preexisted=preexisted, preexisting_entries=preexisting_entries))


def _has_keep_marker(out_dir: Path) -> bool:
    return any((out_dir / name).exists() for name in _KEEP_MARKERS)


def _cleanup_guard(guard: _Guard) -> None:
    if not guard.out_dir.exists():
        return
    if _has_keep_marker(guard.out_dir):
        return
    if guard.preexisted:
        # Pre-existing directory: only remove entries we added during this run.
        # Avoid nuking content the user already had.
        removed_any = False
        for entry in guard.out_dir.iterdir():
            if entry.name in guard.preexisting_entries:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                try:
                    entry.unlink()
                except OSError:
                    continue
            removed_any = True
        if removed_any:
            logger.info("Removed partial outputs from %s", guard.out_dir)
        return
    # Newly created during this run, no keep-marker → remove the whole thing.
    shutil.rmtree(guard.out_dir, ignore_errors=True)
    console.print(f"[dim]Removed empty {guard.out_dir.name}/[/dim]")
    logger.info("Removed empty output directory: %s", guard.out_dir)


def cleanup_pending() -> None:
    """Run cleanup for every registered guard, then clear the registry."""
    while _guards:
        guard = _guards.pop()
        try:
            _cleanup_guard(guard)
        except Exception:
            logger.exception("Failed to clean up %s", guard.out_dir)


def _terminate_handler(signum: int, _frame: object) -> None:
    """Translate a terminating signal into KeyboardInterrupt so finally blocks run."""
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = str(signum)
    raise KeyboardInterrupt(f"Received {name}")


def install_signal_handlers() -> None:
    """Install handlers for SIGTERM/SIGHUP/SIGQUIT (SIGINT already raises KeyboardInterrupt).

    Idempotent. No-op on platforms where a given signal is unavailable (e.g. Windows).
    """
    global _handlers_installed  # noqa: PLW0603  — module-level idempotency flag
    if _handlers_installed:
        return
    for sig_name in ("SIGTERM", "SIGHUP", "SIGQUIT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _terminate_handler)
        except (OSError, ValueError):
            # ValueError if not in main thread; OSError if signal not catchable
            continue
    _handlers_installed = True


def is_clean_exit(exc: BaseException | None) -> bool:
    """Return True for SystemExit with code 0 / None (typer's normal success exit)."""
    if exc is None:
        return True
    if isinstance(exc, SystemExit):
        return exc.code in (0, None)
    return False


__all__ = [
    "cleanup_pending",
    "install_signal_handlers",
    "is_clean_exit",
    "register",
]


def _module_atexit() -> None:
    """Last-resort cleanup if cli.main()'s finally block never ran."""
    if _guards:
        cleanup_pending()


atexit.register(_module_atexit)
