"""Typer CLI entry point and dispatch logic."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from config import Settings
from doc_convert.base import ConvertOptions
from doc_convert.formats import (
    DEFAULT_LOCAL_PRESET,
    DEFAULT_OCR_MODEL,
    LOCAL_OCR_ENGINES,
    LOCAL_PRESETS,
    PRESET_REPO_IDS,
    Engine,
    resolve_captions,
    resolve_ocr_model,
)
from doc_convert.output import make_document_symlink, resolve_output_dir
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


# Heavy enrichment models used by the PDF pipeline when do_chart_extraction
# or do_formula_enrichment is enabled. We pre-fetch them into the Hugging Face
# cache so the first conversion that needs them does not stall.
ENRICHMENT_REPO_IDS: tuple[str, ...] = (
    "ibm-granite/granite-vision-4.1-4b",  # chart extraction (~5 GB)
    "docling-project/CodeFormulaV2",  # formula + code enrichment
)


def _download_enrichments() -> None:
    """Pre-fetch the PDF pipeline enrichment models into the HF cache."""
    from huggingface_hub import snapshot_download  # noqa: PLC0415

    for repo_id in ENRICHMENT_REPO_IDS:
        console.print(f"Downloading [bold]{repo_id}[/bold] into the HF cache...")
        path = snapshot_download(repo_id=repo_id)  # nosec B615 - first-party model repos, latest revision intended
        console.print(f"[green]Done.[/green] {path}")


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
        help="Override output directory",
        show_default="<name>_docling/",
    ),
    llm: str | None = typer.Option(
        None,
        "--llm",
        help=(
            "Remote LLM as 'provider/model'. Used for captions, analysis, "
            "PDF/image --engine llm, and as the fallback for media when "
            "--media-llm is not given. Providers: google, openrouter, ibm."
        ),
        show_default="unset, each step picks its own default",
    ),
    media_llm: str | None = typer.Option(
        None,
        "--media-llm",
        help=(
            "Override the LLM used for audio/video conversion and analysis only. "
            "Takes precedence over --llm for media. Big recordings require the "
            "Gemini Files API; IBM and OpenRouter use inline base64 and will "
            "502 on large files."
        ),
        show_default="google/gemini-3.1-pro-preview",
    ),
    captions: str | None = typer.Option(
        None,
        "--captions",
        help=(
            f"Figure captioner. One of: 'off', a local preset ({', '.join(LOCAL_PRESETS)}), or a 'provider/model' slug."
        ),
        show_default=(
            "same as --llm if set, "
            "else ibm/claude-haiku-4-5 if IBM ICA is configured, "
            "else smolvlm"
        ),
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
        help="Disable OCR (PDF only, --engine local). Alias for --ocr-model off.",
        show_default="off (OCR enabled)",
    ),
    ocr_model: str | None = typer.Option(
        None,
        "--ocr-model",
        help=(
            "OCR engine for the local PDF pipeline (reads text from scanned/image regions). "
            f"One of: 'off', a local engine ({', '.join(LOCAL_OCR_ENGINES)}) or 'local', "
            "or a 'provider/model' slug to read regions with a cloud LLM. "
            "Defaults to a cloud LLM (Gemini); does not auto-inherit --llm."
        ),
        show_default=DEFAULT_OCR_MODEL,
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
        help="Add analysis pass: analyze.md (audio: summary, video: executive brief)",
        show_default="off",
    ),
    meeting_summary: bool = typer.Option(
        False,
        "--meeting-summary",
        help="Audio only: generate summary.html from the transcribed document.md and open it for review.",
        show_default="off",
    ),
    analyze_model: str | None = typer.Option(
        None,
        "--analyze-model",
        help=(
            "Override the LLM used for --analyze only. Takes precedence over --llm "
            "for the analysis step (which sends text only)."
        ),
        show_default="ibm/claude-opus-4-8",
    ),
    analyze_prompt: str | None = typer.Option(
        None,
        "--analyze-prompt",
        help="Custom prompt for --analyze (overrides default adaptive prompt entirely).",
        show_default="built-in adaptive analysis prompt",
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
        help="Meeting name or context for audio/video prompts",
        show_default="audio/video filename stem",
    ),
    instructions: str | None = typer.Option(
        None,
        "-i",
        "--instructions",
        help="Deprecated alias for --analyze-prompt.",
        hidden=True,
    ),
    lang: str | None = typer.Option(
        None,
        "--lang",
        help="Output language for --analyze (e.g. fr, en)",
        show_default="auto-detect from source",
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
    download_enrichments: bool = typer.Option(
        False,
        "--download-enrichments",
        help=(
            "Pre-fetch the PDF pipeline enrichment models (Granite Vision for chart extraction "
            "~5 GB, CodeFormulaV2 for formula enrichment) into the Hugging Face cache so the "
            "first conversion that needs them runs offline."
        ),
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

    effective_prompt = analyze_prompt or instructions
    if instructions and not analyze_prompt:
        console.print("[yellow]--instructions is deprecated; use --analyze-prompt instead.[/yellow]")
    try:
        _dispatch(
            document=document,
            output=output,
            llm=llm,
            media_llm=media_llm,
            captions=captions,
            engine=engine,
            no_ocr=no_ocr,
            ocr_model=ocr_model,
            no_figures=no_figures,
            cpu=cpu,
            all_formats=all_formats,
            start_audio=start_audio,
            analyze=analyze,
            meeting_summary=meeting_summary,
            analyze_model=analyze_model,
            analyze_prompt=effective_prompt,
            analysis_depth=analysis_depth,
            meeting=meeting,
            lang=lang,
            note=note,
            similarity_threshold=similarity_threshold,
            note_force=note_force,
            force=force,
            download_models=download_models,
            download_enrichments=download_enrichments,
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
    ocr_model: str | None,
    no_figures: bool,
    cpu: bool,
    all_formats: bool,
    start_audio: bool,
    analyze: bool,
    meeting_summary: bool,
    analyze_model: str | None,
    analyze_prompt: str | None,
    analysis_depth: int,
    meeting: str | None,
    lang: str | None,
    note: bool,
    similarity_threshold: float,
    note_force: bool,
    force: bool,
    download_models: bool,
    download_enrichments: bool,
) -> None:
    if download_models:
        preset = captions if (captions and "/" not in captions and captions != "off") else DEFAULT_LOCAL_PRESET
        _download_models(preset, Settings())
    if download_enrichments:
        _download_enrichments()
    if download_models or download_enrichments:
        raise typer.Exit()

    # Validate --engine llm requires --llm
    if engine == Engine.LLM and not llm:
        console.print("[red]--engine llm requires --llm <provider/model>[/red]")
        raise typer.Exit(1)

    if meeting_summary and not _is_audio_source(document, start_audio):
        console.print("[red]--meeting-summary only applies to audio sources.[/red]")
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
        captions_spec = resolve_captions(captions, llm, settings)
        _run_media(
            audio_file,
            out_dir,
            "audio",
            meeting or document,
            analyze,
            analyze_prompt,
            lang,
            force,
            llm,
            settings,
            captions_spec=captions_spec,
            media_llm=media_llm,
            analyze_model=analyze_model,
            meeting_summary=meeting_summary,
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
        out_dir = resolve_output_dir(None, downloaded.stem, output)
        _register_output(out_dir)
        try:
            _run_media(
                downloaded,
                out_dir,
                "video",
                meeting,
                analyze,
                analyze_prompt,
                lang,
                force,
                llm,
                settings,
                captions_spec=captions_spec,
                media_llm=media_llm,
                analyze_model=analyze_model,
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
            if ext in (".eml", ".msg"):
                from doc_convert.base import BaseConverter  # noqa: PLC0415
                from doc_convert.output import check_step_cache  # noqa: PLC0415

                out_dir = resolve_output_dir(doc_path, doc_path.stem, output)
                _register_output(out_dir)
                eml_options = ConvertOptions(output_dir=out_dir, llm=llm, settings=settings)
                eml_converter: BaseConverter
                if ext == ".msg":
                    from doc_convert.converters.msg import MsgConverter  # noqa: PLC0415

                    eml_converter = MsgConverter(doc_path, eml_options)
                else:
                    from doc_convert.converters.eml import EmlConverter  # noqa: PLC0415

                    eml_converter = EmlConverter(doc_path, eml_options)
                if not check_step_cache(out_dir, "document.md", force):
                    eml_converter.convert()
                make_document_symlink(out_dir)
                if (
                    analyze
                    and not check_step_cache(out_dir, "analyze.md", force)
                    and eml_converter.run_analysis(
                        analyze_model or llm, analyze_prompt, meeting, lang, depth=analysis_depth
                    )
                ):
                    console.print("  analyze.md")
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
                    analyze_prompt,
                    lang,
                    force,
                    llm,
                    settings,
                    captions_spec=captions_spec,
                    media_llm=media_llm,
                    analyze_model=analyze_model,
                    meeting_summary=meeting_summary,
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
                    analyze_prompt,
                    lang,
                    force,
                    llm,
                    settings,
                    captions_spec=captions_spec,
                    media_llm=media_llm,
                    analyze_model=analyze_model,
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
            ocr=resolve_ocr_model(ocr_model, no_ocr=no_ocr),
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
        make_document_symlink(out_dir)

        # Step 2: Analysis (skip if analyze.md exists)
        if (
            analyze
            and not check_step_cache(out_dir, "analyze.md", force)
            and converter.run_analysis(analyze_model or llm, analyze_prompt, meeting, lang, depth=analysis_depth)
        ):
            console.print("  analyze.md")

        # Step 3: Note (skip if note_sent exists)
        if note and not check_step_cache(out_dir, "note_sent", note_force):
            from doc_convert.notes import create_note_from_conversion  # noqa: PLC0415

            create_note_from_conversion(
                out_dir, settings, source_path=doc_path, lang=lang, similarity_threshold=similarity_threshold
            )
    finally:
        if tmp_file and tmp_file.exists():
            tmp_file.unlink()


def _is_audio_source(document: str | None, start_audio: bool) -> bool:
    """True when the input is (or will become) an audio file."""
    if start_audio:
        return True
    if not document:
        return False
    from media_llm import is_audio_ext  # noqa: PLC0415

    return is_audio_ext(Path(document).suffix.lower())


def _run_media(
    media_path: Path,
    output_dir: Path,
    media_type: str,
    meeting: str | None,
    analyze: bool,
    analyze_prompt: str | None,
    lang: str | None,
    force: bool,
    llm: str | None,
    settings: Settings,
    *,
    captions_spec: object = None,
    media_llm: str | None = None,
    analyze_model: str | None = None,
    meeting_summary: bool = False,
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

    from doc_convert.formats import CaptionsLlm, CaptionsLocal, CaptionsOff  # noqa: PLC0415

    options_kwargs: dict[str, object] = {
        "output_dir": output_dir,
        "llm": media_target,
        "settings": settings,
    }
    if isinstance(captions_spec, (CaptionsOff, CaptionsLocal, CaptionsLlm)):
        options_kwargs["captions"] = captions_spec
    options = ConvertOptions(**options_kwargs)  # type: ignore[arg-type]
    converter = MediaConverter(
        media_path,
        options,
        media_type=media_type,
        meeting=meeting,
        instructions=analyze_prompt,
        lang=lang,
        llm=media_target,
    )

    # Step 1: Conversion (skip if document.md exists)
    if not check_step_cache(output_dir, "document.md", force):
        converter.convert()
    make_document_symlink(output_dir)

    # Step 2: Analysis (skip if analyze.md exists)
    if (
        analyze
        and not check_step_cache(output_dir, "analyze.md", force)
        and converter.run_analysis(analyze_model or media_target, analyze_prompt, meeting, lang)
    ):
        console.print("  analyze.md")

    # Step 2b: Meeting summary HTML (audio only).
    if meeting_summary and media_type == "audio" and not check_step_cache(output_dir, "summary.html", force):
        from doc_convert.meeting_summary import generate_meeting_summary  # noqa: PLC0415

        if generate_meeting_summary(
            output_dir,
            settings,
            analyze_model=analyze_model,
            meeting_context=meeting,
            lang=lang,
        ):
            console.print("  summary.html")

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
