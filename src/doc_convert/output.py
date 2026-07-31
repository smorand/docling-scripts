"""Output directory helpers: cache check, path resolution, summary printing."""

from __future__ import annotations

import logging
from pathlib import Path

from logging_config import console

logger = logging.getLogger(__name__)


def print_output_summary(
    output_dir: Path,
    fig_count: int = 0,
    all_formats: bool = False,
    vlm_used: bool = False,
    desc_count: int = 0,
    extra_files: list[str] | None = None,
) -> None:
    """Print a consistent output summary."""
    console.print(f"[green]Output:[/green] {output_dir}/")
    console.print("  document.md")
    if fig_count > 0:
        console.print("  images.md")
        console.print(f"  figures/     ({fig_count} figure(s))")
        if vlm_used:
            console.print(f"  VLM descriptions: {desc_count}/{fig_count}")
    for f in extra_files or []:
        console.print(f"  {f}")
    if all_formats:
        console.print("  output.*     (md, html, json, txt)")


def resolve_output_dir(source_path: Path | None, name: str, output_override: str | None) -> Path:
    """Compute the <name>_docling/ output directory."""
    if output_override:
        return Path(output_override)
    parent = source_path.parent if source_path else Path.cwd()
    return parent / f"{name}_docling"


def check_cache(out_path: Path, force: bool) -> bool:
    """Return True if output exists and force is False (should skip).

    .. deprecated:: Use :func:`check_step_cache` for per-step caching.
    """
    if out_path.exists() and not force:
        has_content = any(out_path.iterdir()) if out_path.is_dir() else out_path.stat().st_size > 0
        if has_content:
            console.print(f"[yellow]Output already exists:[/yellow] {out_path}")
            console.print("[dim]Use -f to force re-conversion[/dim]")
            return True
    return False


def check_step_cache(out_dir: Path, filename: str, force: bool) -> bool:
    """Return True if a specific step output file exists and force is False."""
    return (out_dir / filename).exists() and not force


_DOCLING_SUFFIX = "_docling"


def make_document_symlink(output_dir: Path, *, symlink: bool = False) -> None:
    """Create ``<output_dir_stem>.md`` next to ``output_dir`` as a symlink to
    ``<output_dir>/document.md``, so the user can open the converted document
    without diving into the ``_docling/`` folder.

    Safety:
        - Skips if ``document.md`` is missing (conversion failed or pending).
        - Refuses to overwrite a pre-existing regular file (e.g. a companion
          ``.md`` next to an audio source); only existing symlinks are
          refreshed.
    """
    if not symlink:
        return

    target = output_dir / "document.md"
    if not target.exists():
        return

    name = output_dir.name
    stem = name[: -len(_DOCLING_SUFFIX)] if name.endswith(_DOCLING_SUFFIX) else name
    if not stem:
        return

    link = output_dir.parent / f"{stem}.md"
    relative_target = Path(output_dir.name) / "document.md"

    if link.is_symlink():
        link.unlink()
    elif link.exists():
        logger.debug("Not overwriting existing file %s with a doc symlink", link)
        return

    try:
        link.symlink_to(relative_target)
    except OSError as exc:
        logger.warning("Could not create symlink %s -> %s: %s", link, relative_target, exc)
