"""Typer CLI entry point and dispatch logic."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from config import Settings
from doc_convert.base import ConvertOptions
from doc_convert.formats import (
    DEFAULT_LOCAL_PRESET,
    LOCAL_PRESETS,
    PRESET_REPO_IDS,
    Engine,
    resolve_captions,
)
from doc_convert.output import resolve_output_dir
from doc_convert.output_guard import cleanup_pending as _cleanup_outputs
from doc_convert.output_guard import install_signal_handlers as _install_signal_handlers
from doc_convert.output_guard import register as _register_output
from logging_config import console, setup_logging
from tracing import configure_tracing

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Convert documents to markdown using Docling.",
    no_args_is_help=True,
)


def _download_models(preset: str, settings: Settings) -> None:
    """Download a local captioner model for offline use."""
    from docling.models.utils.hf_model_download import download_hf_model  # noqa: PLC0415

    if preset not in PRESET_REPO_IDS:
        console.print(f"[red]Unknown captions model '{preset}'. Choose one of: {', '.join(LOCAL_PRESETS)}[/red]")
        raise typer.Exit(1)

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
def main(
    document: str = typer.Argument(
        None,
        help="File path or URL. Required unless --start-audio or --download-models is used.",
    ),
    output: str | None = typer.Option(
        None,
        "-o",
        "--output",
        help="Override output directory. Default: <name>_docling/",
        show_default=False,
    ),
    llm: str | None = typer.Option(
        None,
        "--llm",
        help=(
            "Remote LLM as 'provider/model'. Used for captions, analysis, "
            "PDF/image --engine llm, and as the fallback for media when "
            "--media-llm is not given. Providers: google, openrouter, ibm. "
            "Default: unset (each step picks its own default)."
        ),
        show_default=False,
    ),
    media_llm: str | None = typer.Option(
        None,
        "--media-llm",
        help=(
            "Override the LLM used for audio/video conversion and analysis only. "
            "Takes precedence over --llm for media. Big recordings require the "
            "Gemini Files API; IBM and OpenRouter use inline base64 and will "
            "502 on large files. Default: google/gemini-3.1-pro-preview."
        ),
        show_default=False,
    ),
    captions: str | None = typer.Option(
        None,
        "--captions",
        help=(
            f"Figure captioner. One of: 'off', a local preset ({', '.join(LOCAL_PRESETS)}), "
            "or a 'provider/model' slug. Default: same as --llm if set, else "
            "ibm/claude-haiku-4-5 if IBM ICA is configured, else "
            "google/gemini-3.1-flash-lite-preview if GOOGLE_API_KEY is set, else smolvlm."
        ),
        show_default=False,
    ),
    engine: Engine = typer.Option(
        Engine.LOCAL,
        "--engine",
        help=(
            "Body extraction for PDF/image: 'local' uses Docling layout+OCR+tables; "
            "'llm' rasterizes each page and sends it to --llm. Images always use 'llm'."
        ),
    ),
    no_ocr: bool = typer.Option(
        False,
        "--no-ocr",
        help="Disable OCR (PDF only, --engine local)",
        show_default="off (OCR enabled)",
    ),
    no_figures: bool = typer.Option(
        False,
        "--no-figures",
        help="Skip figure extraction (text + tables only, faster)",
        show_default="off (figures extracted)",
    ),
    cpu: bool = typer.Option(
        False,
        "--cpu",
        help="Force CPU for the Docling pipeline (workaround for MPS float64 errors on Apple Silicon)",
        show_default="off (auto-fallback to CPU on MPS errors)",
    ),
    all_formats: bool = typer.Option(
        False,
        "--all",
        help="Export all formats (md, html, json, txt) in addition to document.md",
        show_default="off",
    ),
    start_audio: bool = typer.Option(
        False,
        "--start-audio",
        help="Record from microphone (Ctrl+C to stop). DOCUMENT becomes the name.",
        show_default="off",
    ),
    analyze: bool = typer.Option(
        False,
        "--analyze",
        help="Add analysis pass: analysis.md (audio: summary, video: executive brief)",
        show_default="off",
    ),
    analysis_depth: int = typer.Option(
        5,
        "--analysis-depth",
        help="Analysis depth level 1-5 (1=executive brief, 3=standard, 5=comprehensive)",
        min=1,
        max=5,
    ),
    meeting: str | None = typer.Option(
        None,
        "-m",
        "--meeting",
        help="Meeting name or context for audio/video prompts. Default: use the audio/video filename stem.",
        show_default=False,
    ),
    instructions: str | None = typer.Option(
        None,
        "-i",
        "--instructions",
        help="Custom prompt for --analyze (overrides default analysis prompt). Default: built-in analysis prompt.",
        show_default=False,
    ),
    lang: str | None = typer.Option(
        None,
        "--lang",
        help="Output language for --analyze (e.g. fr, en). Default: auto-detect from source.",
        show_default=False,
    ),
    note: bool = typer.Option(
        False,
        "--note",
        help="Store a note in the Notes system from the conversion output",
        show_default="off",
    ),
    similarity_threshold: float = typer.Option(
        0.85,
        "--similarity-threshold",
        help="Similarity score threshold for note deduplication (0.0-1.0)",
    ),
    note_force: bool = typer.Option(
        False,
        "--note-force",
        help="Force note creation even if a similar note exists",
        show_default="off",
    ),
    force: bool = typer.Option(
        False,
        "-f",
        "--force",
        help="Force re-conversion even if output already exists",
        show_default="off",
    ),
    download_models: bool = typer.Option(
        False,
        "--download-models",
        help="Download the local captioner model named by --captions (must be a preset) for offline use",
        show_default="off",
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
        show_default="off",
    ),
) -> None:
    """Convert documents, audio, and video to markdown.

    Output always goes to <name>_docling/ directory with document.md as main file.
    Use -o to override the output directory.

    \b
    Documents:        doc-convert document.pdf
    PDF + LLM caps:   doc-convert document.pdf --llm openrouter/anthropic/claude-haiku-4.5
    PDF rasterized:   doc-convert document.pdf --llm google/gemini-3.1-pro-preview --engine llm
    Image:            doc-convert photo.png --llm google/gemini-3.1-pro-preview
    Audio:            doc-convert meeting.ogg [--analyze]
    Video:            doc-convert video.mp4 [--analyze]
    YouTube:          doc-convert https://youtube.com/watch?v=...
    Record:           doc-convert --start-audio "Meeting Name"
    Models:           doc-convert --download-models [--captions qwen]
    """
    setup_logging(verbose=verbose, quiet=quiet, source_path=document)
    _install_signal_handlers()

    try:
        _dispatch(
            document=document,
            output=output,
            llm=llm,
            media_llm=media_llm,
            captions=captions,
            engine=engine,
            no_ocr=no_ocr,
            no_figures=no_figures,
            cpu=cpu,
            all_formats=all_formats,
            start_audio=start_audio,
            analyze=analyze,
            analysis_depth=analysis_depth,
            meeting=meeting,
            instructions=instructions,
            lang=lang,
            note=note,
            similarity_threshold=similarity_threshold,
            note_force=note_force,
            force=force,
            download_models=download_models,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted; cleaning up incomplete outputs[/yellow]")
        raise typer.Exit(130) from None
    finally:
        _cleanup_outputs()


def _dispatch(  # noqa: PLR0912, PLR0915
    *,
    document: str | None,
    output: str | None,
    llm: str | None,
    media_llm: str | None,
    captions: str | None,
    engine: Engine,
    no_ocr: bool,
    no_figures: bool,
    cpu: bool,
    all_formats: bool,
    start_audio: bool,
    analyze: bool,
    analysis_depth: int,
    meeting: str | None,
    instructions: str | None,
    lang: str | None,
    note: bool,
    similarity_threshold: float,
    note_force: bool,
    force: bool,
    download_models: bool,
) -> None:
    if download_models:
        preset = captions if (captions and "/" not in captions and captions != "off") else DEFAULT_LOCAL_PRESET
        _download_models(preset, Settings())
        raise typer.Exit()

    # Validate --engine llm requires --llm
    if engine == Engine.LLM and not llm:
        console.print("[red]--engine llm requires --llm <provider/model>[/red]")
        raise typer.Exit(1)

    # ── Audio recording mode ─────────────────────────────────────────────
    if start_audio:
        if not document:
            console.print('[red]--start-audio requires a name: doc-convert --start-audio "Meeting Name"[/red]')
            raise typer.Exit(1)

        from audio import record_audio  # noqa: PLC0415

        configure_tracing("doc-convert", source_path=document)
        settings = Settings()
        out_dir = resolve_output_dir(None, document, output)
        _register_output(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        audio_file = record_audio(out_dir / "audio.ogg")
        _run_media(
            audio_file,
            out_dir,
            "audio",
            meeting or document,
            analyze,
            instructions,
            lang,
            force,
            llm,
            settings,
            media_llm=media_llm,
            companion_name_override=document,
            note=note,
            note_force=note_force,
            similarity_threshold=similarity_threshold,
        )
        raise typer.Exit()

    if document is None:
        console.print("Missing argument 'DOCUMENT'. See --help.")
        raise typer.Exit(1)

    configure_tracing("doc-convert")
    settings = Settings()

    captions_spec = resolve_captions(captions, llm, settings)

    # ── YouTube URL ──────────────────────────────────────────────────────
    from video import is_youtube_url  # noqa: PLC0415

    if is_youtube_url(document):
        from video import download_youtube  # noqa: PLC0415

        downloaded = download_youtube(document)
        out_dir = resolve_output_dir(Path.cwd(), downloaded.stem, output)
        _register_output(out_dir)
        try:
            _run_media(
                downloaded,
                out_dir,
                "video",
                meeting,
                analyze,
                instructions,
                lang,
                force,
                llm,
                settings,
                media_llm=media_llm,
                note=note,
                similarity_threshold=similarity_threshold,
            )
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
            if ext == ".eml":
                from doc_convert.converters.eml import EmlConverter  # noqa: PLC0415
                from doc_convert.output import check_step_cache  # noqa: PLC0415

                out_dir = resolve_output_dir(doc_path, doc_path.stem, output)
                _register_output(out_dir)
                eml_options = ConvertOptions(output_dir=out_dir, settings=settings)
                eml_converter = EmlConverter(doc_path, eml_options)
                if not check_step_cache(out_dir, "document.md", force):
                    eml_converter.convert()
                if (
                    analyze
                    and not check_step_cache(out_dir, "analysis.md", force)
                    and eml_converter.run_analysis(llm, instructions, meeting, lang, depth=analysis_depth)
                ):
                    console.print("  analysis.md")
                if note and not check_step_cache(out_dir, "note_sent", note_force):
                    from doc_convert.notes import create_note_from_conversion  # noqa: PLC0415

                    create_note_from_conversion(
                        out_dir,
                        settings,
                        source_path=doc_path,
                        lang=lang,
                        similarity_threshold=similarity_threshold,
                        note_force=note_force,
                    )
                raise typer.Exit()
            if is_audio_ext(ext):
                out_dir = resolve_output_dir(doc_path, doc_path.stem, output)
                _register_output(out_dir)
                _run_media(
                    doc_path,
                    out_dir,
                    "audio",
                    meeting,
                    analyze,
                    instructions,
                    lang,
                    force,
                    llm,
                    settings,
                    media_llm=media_llm,
                    note=note,
                    similarity_threshold=similarity_threshold,
                )
                raise typer.Exit()
            if is_video_ext(ext):
                out_dir = resolve_output_dir(doc_path, doc_path.stem, output)
                _register_output(out_dir)
                _run_media(
                    doc_path,
                    out_dir,
                    "video",
                    meeting,
                    analyze,
                    instructions,
                    lang,
                    force,
                    llm,
                    settings,
                    media_llm=media_llm,
                    note=note,
                    similarity_threshold=similarity_threshold,
                )
                raise typer.Exit()

            from doc_convert.formats import detect_format  # noqa: PLC0415

            fmt = detect_format(doc_path)
            doc_name = doc_path.stem

        from doc_convert.output import check_step_cache  # noqa: PLC0415

        out_dir = resolve_output_dir(doc_path, doc_name, output)
        _register_output(out_dir)

        # ── Companion file detection ────────────────────────────────────
        from doc_convert.companion import load_companion_context  # noqa: PLC0415

        companion_ctx = load_companion_context(doc_path, out_dir, llm, settings, force=force, lang=lang)
        if companion_ctx:
            meeting = f"{companion_ctx}\n\n{meeting}" if meeting else companion_ctx

        options = ConvertOptions(
            output_dir=out_dir,
            figures=not no_figures,
            all_formats=all_formats,
            do_ocr=not no_ocr,
            cpu=cpu,
            engine=engine,
            captions=captions_spec,
            llm=llm,
            settings=settings,
        )

        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415

        from doc_convert.base import BaseConverter  # noqa: PLC0415

        converter: BaseConverter | None = None

        if fmt == InputFormat.IMAGE:
            from doc_convert.converters.image import ImageConverter  # noqa: PLC0415

            if not llm:
                console.print("[red]Image conversion requires --llm <provider/model>[/red]")
                raise typer.Exit(1)
            converter = ImageConverter(doc_path, options)
        elif fmt == InputFormat.PDF and engine == Engine.LLM:
            from doc_convert.converters.image import ImageConverter  # noqa: PLC0415

            converter = ImageConverter(doc_path, options)
        elif fmt == InputFormat.PDF:
            from doc_convert.converters.pdf import PdfConverter  # noqa: PLC0415

            converter = PdfConverter(doc_path, options)
        elif fmt == InputFormat.PPTX:
            from doc_convert.converters.pptx import PptxConverter  # noqa: PLC0415

            converter = PptxConverter(doc_path, options)
        elif fmt == InputFormat.DOCX:
            from doc_convert.converters.docx import DocxConverter  # noqa: PLC0415

            converter = DocxConverter(doc_path, options)
        elif fmt == InputFormat.XLSX:
            from doc_convert.converters.xlsx import XlsxConverter  # noqa: PLC0415

            converter = XlsxConverter(doc_path, options)
        else:
            console.print(f"[red]Unsupported format: {fmt}[/red]")
            raise typer.Exit(1)

        # Step 1: Conversion (skip if document.md exists)
        if not check_step_cache(out_dir, "document.md", force):
            converter.convert()

        # Step 2: Analysis (skip if analysis.md exists)
        if (
            analyze
            and not check_step_cache(out_dir, "analysis.md", force)
            and converter.run_analysis(llm, instructions, meeting, lang, depth=analysis_depth)
        ):
            console.print("  analysis.md")

        # Step 3: Note (skip if note_sent exists)
        if note and not check_step_cache(out_dir, "note_sent", note_force):
            from doc_convert.notes import create_note_from_conversion  # noqa: PLC0415

            create_note_from_conversion(
                out_dir, settings, source_path=doc_path, lang=lang, similarity_threshold=similarity_threshold
            )
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
    lang: str | None,
    force: bool,
    llm: str | None,
    settings: Settings,
    *,
    media_llm: str | None = None,
    companion_name_override: str | None = None,
    note: bool = False,
    note_force: bool = False,
    similarity_threshold: float = 0.85,
) -> None:
    """Dispatch to MediaConverter with independent step caching.

    ``media_llm`` (if set) overrides the model used for the actual media
    payload (audio/video → LLM). Otherwise falls back to ``llm`` then to the
    media default. Companion context analysis still uses ``llm`` (text path).
    """
    from doc_convert.companion import load_companion_context  # noqa: PLC0415
    from doc_convert.converters.media import MediaConverter  # noqa: PLC0415
    from doc_convert.output import check_step_cache  # noqa: PLC0415

    media_target = media_llm or llm

    companion_ctx = load_companion_context(
        media_path,
        output_dir,
        llm,
        settings,
        force=force,
        name_override=companion_name_override,
        lang=lang,
    )
    if companion_ctx:
        meeting = f"{companion_ctx}\n\n{meeting}" if meeting else companion_ctx

    options = ConvertOptions(output_dir=output_dir, llm=media_target, settings=settings)
    converter = MediaConverter(
        media_path,
        options,
        media_type=media_type,
        meeting=meeting,
        instructions=instructions,
        lang=lang,
        llm=media_target,
    )

    # Step 1: Conversion (skip if document.md exists)
    if not check_step_cache(output_dir, "document.md", force):
        converter.convert()

    # Step 2: Analysis (skip if analysis.md exists)
    if (
        analyze
        and not check_step_cache(output_dir, "analysis.md", force)
        and converter.run_analysis(media_target, instructions, meeting, lang)
    ):
        console.print("  analysis.md")

    # Step 3: Note (skip if note_sent exists)
    if note and not check_step_cache(output_dir, "note_sent", note_force):
        from doc_convert.notes import create_note_from_conversion  # noqa: PLC0415

        create_note_from_conversion(
            output_dir,
            settings,
            source_path=media_path,
            companion_name_override=companion_name_override,
            lang=lang,
            similarity_threshold=similarity_threshold,
            note_force=note_force,
        )
