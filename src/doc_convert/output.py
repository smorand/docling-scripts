"""Output directory helpers: cache check, path resolution, summary printing."""

from __future__ import annotations

from pathlib import Path

from logging_config import console


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
    """Return True if output exists and force is False (should skip)."""
    if out_path.exists() and not force:
        has_content = any(out_path.iterdir()) if out_path.is_dir() else out_path.stat().st_size > 0
        if has_content:
            console.print(f"[yellow]Output already exists:[/yellow] {out_path}")
            console.print("[dim]Use -f to force re-conversion[/dim]")
            return True
    return False
