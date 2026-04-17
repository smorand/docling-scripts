"""Typer CLI entry point and dispatch logic."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from config import Settings
from doc_convert.base import ConvertOptions
from doc_convert.formats import PRESET_REPO_IDS, VlmPreset
from doc_convert.output import check_cache, resolve_output_dir
from logging_config import console, setup_logging
from tracing import configure_tracing

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Convert documents to markdown using Docling.",
    no_args_is_help=True,
)


def _download_models(preset: str, settings: Settings) -> None:
    """Download VLM model for offline use."""
    from docling.models.utils.hf_model_download import download_hf_model  # noqa: PLC0415

    dest = Path(settings.models_path)
    dest.mkdir(parents=True, exist_ok=True)

    repo_id = PRESET_REPO_IDS[preset]
    cache_folder = repo_id.replace("/", "--")
    target = dest / cache_folder

    if target.exists():
        console.print(f"[yellow]Already downloaded:[/yellow] {target}")
        return

    console.print(f"Downloading [bold]{repo_id}[/bold] to {dest}/")
    download_hf_model(repo_id=repo_id, local_dir=target, progress=True)
    console.print(f"[green]Done.[/green] Model available at {target}")


@app.command()
def main(  # noqa: PLR0912, PLR0915
    document: str = typer.Argument(
        None,
        help="File path or URL. Required unless --start-audio or --download-models is used.",
    ),
    output: str | None = typer.Option(
        None,
        "-o",
        "--output",
        help="Override output directory (default: <name>_docling/)",
    ),
    use_external_llm: str | None = typer.Option(
        None,
        "--use-external-llm",
        help="External LLM provider: google/<model> or openrouter/<model>",
    ),
    vlm_preset: VlmPreset = typer.Option(
        VlmPreset.smolvlm,
        "--vlm-preset",
        help="Local VLM preset for picture descriptions",
    ),
    no_ocr: bool = typer.Option(
        False,
        "--no-ocr",
        help="Disable OCR (PDF only)",
    ),
    no_vlm: bool = typer.Option(
        False,
        "--no-vlm",
        help="Disable VLM picture descriptions (PDF, DOCX, PPTX)",
    ),
    no_figures: bool = typer.Option(
        False,
        "--no-figures",
        help="Skip figure extraction (text + tables only, faster)",
    ),
    all_formats: bool = typer.Option(
        False,
        "--all",
        help="Export all formats (md, html, json, txt) in addition to document.md",
    ),
    start_audio: bool = typer.Option(
        False,
        "--start-audio",
        help="Record from microphone (Ctrl+C to stop). DOCUMENT becomes the name.",
    ),
    analyze: bool = typer.Option(
        False,
        "--analyze",
        help="Add analysis pass: analysis.md (audio: summary, video: executive brief)",
    ),
    meeting: str | None = typer.Option(
        None,
        "-m",
        "--meeting",
        help="Meeting name or context for audio/video prompts",
    ),
    instructions: str | None = typer.Option(
        None,
        "-i",
        "--instructions",
        help="Custom prompt for --analyze (overrides default analysis prompt)",
    ),
    force: bool = typer.Option(
        False,
        "-f",
        "--force",
        help="Force re-conversion even if output already exists",
    ),
    download_models: bool = typer.Option(
        False,
        "--download-models",
        help="Download the VLM model selected by --vlm-preset for offline use",
    ),
    verbose: int = typer.Option(
        0,
        "-v",
        "--verbose",
        count=True,
        help="Increase verbosity (-vv for debug)",
    ),
    quiet: bool = typer.Option(
        False,
        "-q",
        "--quiet",
        help="Only show warnings and errors",
    ),
) -> None:
    """Convert documents, audio, and video to markdown.

    Output always goes to <name>_docling/ directory with document.md as main file.
    Use -o to override the output directory.

    \b
    Documents:  doc-convert document.pdf
    Audio:      doc-convert meeting.ogg [--analyze]
    Video:      doc-convert video.mp4 [--analyze]
    YouTube:    doc-convert https://youtube.com/watch?v=...
    Record:     doc-convert --start-audio "Meeting Name"
    Models:     doc-convert --download-models
    """
    setup_logging(verbose=verbose, quiet=quiet)

    if download_models:
        _download_models(vlm_preset.value, Settings())
        raise typer.Exit()

    # ── Audio recording mode ─────────────────────────────────────────────
    if start_audio:
        if not document:
            console.print('[red]--start-audio requires a name: doc-convert --start-audio "Meeting Name"[/red]')
            raise typer.Exit(1)

        from audio import record_audio  # noqa: PLC0415

        configure_tracing("doc-convert")
        settings = Settings()
        out_dir = resolve_output_dir(None, document, output)
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_file = record_audio(out_dir / "audio.ogg")
        _run_media(
            audio_file, out_dir, "audio", meeting or document, analyze, instructions, force, use_external_llm, settings
        )
        raise typer.Exit()

    if document is None:
        console.print("Missing argument 'DOCUMENT'. See --help.")
        raise typer.Exit(1)

    configure_tracing("doc-convert")
    settings = Settings()

    from doc_convert.providers import parse_external_llm  # noqa: PLC0415

    ext_llm = parse_external_llm(use_external_llm) if use_external_llm else None

    # ── YouTube URL ──────────────────────────────────────────────────────
    from video import is_youtube_url  # noqa: PLC0415

    if is_youtube_url(document):
        from video import download_youtube  # noqa: PLC0415

        downloaded = download_youtube(document)
        out_dir = resolve_output_dir(Path.cwd(), downloaded.stem, output)
        try:
            _run_media(downloaded, out_dir, "video", meeting, analyze, instructions, force, use_external_llm, settings)
        finally:
            downloaded.unlink(missing_ok=True)
        raise typer.Exit()

    # ── File-based conversion ────────────────────────────────────────────
    from doc_convert.google_docs import download_google_doc, is_google_url  # noqa: PLC0415

    tmp_file: Path | None = None
    try:
        if is_google_url(document):
            tmp_file, title, fmt = download_google_doc(document, settings)
            doc_path = tmp_file
            doc_name = title
        else:
            doc_path = Path(document)
            if not doc_path.exists():
                console.print(f"[red]{doc_path} not found[/red]")
                raise typer.Exit(1)

            from media_llm import is_audio_ext, is_video_ext  # noqa: PLC0415

            ext = doc_path.suffix.lower()
            if is_audio_ext(ext):
                out_dir = resolve_output_dir(doc_path, doc_path.stem, output)
                _run_media(
                    doc_path, out_dir, "audio", meeting, analyze, instructions, force, use_external_llm, settings
                )
                raise typer.Exit()
            if is_video_ext(ext):
                out_dir = resolve_output_dir(doc_path, doc_path.stem, output)
                _run_media(
                    doc_path, out_dir, "video", meeting, analyze, instructions, force, use_external_llm, settings
                )
                raise typer.Exit()

            from doc_convert.formats import detect_format  # noqa: PLC0415

            fmt = detect_format(doc_path)
            doc_name = doc_path.stem

        out_dir = resolve_output_dir(doc_path, doc_name, output)
        if check_cache(out_dir, force):
            raise typer.Exit()

        options = ConvertOptions(
            output_dir=out_dir,
            vlm=not no_vlm,
            vlm_preset=vlm_preset.value,
            figures=not no_figures,
            all_formats=all_formats,
            do_ocr=not no_ocr,
            external_llm=ext_llm,
            settings=settings,
        )

        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

        if fmt == InputFormat.PDF and not ext_llm:
            from doc_convert.converters.pdf import PdfConverter  # noqa: PLC0415

            PdfConverter(doc_path, options).convert()
        elif fmt == InputFormat.PPTX:
            from doc_convert.converters.pptx import PptxConverter  # noqa: PLC0415

            PptxConverter(doc_path, options).convert()
        elif fmt == InputFormat.DOCX:
            from doc_convert.converters.docx import DocxConverter  # noqa: PLC0415

            DocxConverter(doc_path, options).convert()
        elif fmt == InputFormat.XLSX:
            from doc_convert.converters.xlsx import XlsxConverter  # noqa: PLC0415

            XlsxConverter(doc_path, options).convert()
        elif fmt in (InputFormat.IMAGE, InputFormat.PDF):
            from doc_convert.converters.image import ImageConverter  # noqa: PLC0415

            ImageConverter(doc_path, options).convert()
        else:
            console.print(f"[red]Unsupported format: {fmt}[/red]")
            raise typer.Exit(1)
    finally:
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()


def _run_media(
    media_path: Path,
    output_dir: Path,
    media_type: str,
    meeting: str | None,
    analyze: bool,
    instructions: str | None,
    force: bool,
    use_external_llm: str | None,
    settings: Settings,
) -> None:
    """Dispatch to MediaConverter."""
    from doc_convert.converters.media import MediaConverter  # noqa: PLC0415
    from doc_convert.output import check_cache  # noqa: PLC0415

    if check_cache(output_dir, force):
        return

    options = ConvertOptions(output_dir=output_dir, settings=settings)
    MediaConverter(
        media_path,
        options,
        media_type=media_type,
        meeting=meeting,
        analyze=analyze,
        instructions=instructions,
        use_external_llm=use_external_llm,
    ).convert()
