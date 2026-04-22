"""Logging configuration with Rich console output."""

from __future__ import annotations

import logging
import warnings

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)

# Suppress noisy third-party warnings
warnings.filterwarnings("ignore", message="Palette images with Transparency", category=UserWarning)
warnings.filterwarnings("ignore", message="Passing `generation_config` together with generation-related")


def setup_logging(*, verbose: int = 0, quiet: bool = False) -> None:
    """Configure logging with Rich handler.

    Args:
        verbose: Verbosity level (0=INFO, 1=DEBUG, 2+=TRACE).
        quiet: If True, only show warnings and errors.
    """
    if quiet:
        level = logging.WARNING
    elif verbose >= 1:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, show_path=False, rich_tracebacks=True, omit_repeated_times=False)],
    )
