"""Logging configuration with Rich console output and file logging."""

from __future__ import annotations

import hashlib
import logging
import warnings
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)

LOG_DIR = Path.home() / ".local" / "share" / "doc-convert"

# Suppress noisy third-party warnings
warnings.filterwarnings("ignore", message="Palette images with Transparency", category=UserWarning)
warnings.filterwarnings("ignore", message="Passing `generation_config` together with generation-related")


def setup_logging(*, verbose: int = 0, quiet: bool = False, source_path: str | None = None) -> None:
    """Configure logging with Rich handler + file handler.

    Logs are written to ~/.local/share/doc-convert/doc-convert-{datetime}-{hash}.log
    where hash is the MD5 of the source file path (first 8 chars).

    Args:
        verbose: Verbosity level (0=INFO, 1=DEBUG, 2+=TRACE).
        quiet: If True, only show warnings and errors.
        source_path: Source file path (used in log filename hash).
    """
    if quiet:
        level = logging.WARNING
    elif verbose >= 1:
        level = logging.DEBUG
    else:
        level = logging.INFO

    handlers: list[logging.Handler] = [
        RichHandler(console=console, show_path=False, rich_tracebacks=True, omit_repeated_times=False),
    ]

    # File handler
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path_hash = hashlib.md5((source_path or "none").encode()).hexdigest()[:8]
    log_file = LOG_DIR / f"doc-convert-{timestamp}-{path_hash}.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    file_handler.setLevel(logging.DEBUG)
    handlers.append(file_handler)

    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)

    logging.getLogger(__name__).debug("Logging to %s", log_file)
