# docling-scripts

## Overview

Unified document converter (PDF, images, DOCX, XLSX, PPTX, audio, video, Google Docs/Sheets) powered by Docling.
Tech stack: Python 3.10+, Typer, Docling, python-pptx, httpx, pydantic-settings, Rich, OpenTelemetry.

## Key Commands

```
make sync          # Install dependencies
make run ARGS='document.pdf'  # Run via uv
make check         # Full quality gate (lint, format, typecheck, security, tests)
make install       # Install as uv tool (system-wide: doc-convert)
make uninstall     # Remove uv tool
doc-convert --download-models                                  # Pre-download default captioner (smolvlm)
doc-convert document.pdf                                       # Default: local pipeline, local captions
doc-convert document.pdf --llm openrouter/anthropic/claude-haiku-4.5   # Hybrid: local body + LLM captions
doc-convert document.pdf --llm google/gemini-3.1-pro-preview --engine llm   # Full-page rasterize via LLM
doc-convert photo.png --llm google/gemini-3.1-pro-preview      # Image conversion
doc-convert --start-audio "Meeting Name"                       # Record + transcribe
doc-convert video.mp4 --analyze                                # Extract + summarize
```

### Flag surface (3 axes)

- `--llm <provider/model>` — remote model identity (captions, document analysis, `--engine llm`, and as fallback for media when `--media-llm` is not set).
- `--media-llm <provider/model>` — overrides the LLM used for audio/video conversion + media analysis only. Precedence: `--media-llm` > `--llm` > `google/gemini-3.1-pro-preview` (Files API default; required for big recordings).
- `--captions <off|preset|provider/model>` — figure captioner. Defaults to `--llm` if set, else first cloud with creds from `AUTO_CAPTIONS_PREFERENCES` (today: `ibm/claude-haiku-4-5` → `google/gemini-3.1-flash-lite-preview`), else local `smolvlm`.
- `--engine local|llm` — PDF/image body parser. `local` (default for PDF) uses Docling; `llm` rasterizes pages to `--llm`. Images always `llm`.

## Project Structure

```
src/
├── doc_convert/          # Main package
│   ├── __init__.py       # Re-exports app
│   ├── cli.py            # Typer CLI entry point + dispatch
│   ├── base.py           # BaseConverter + ConvertOptions
│   ├── converters/       # One class per document type
│   │   ├── pdf.py        # PdfConverter (local Docling pipeline; captions via base.describe_figures)
│   │   ├── docx.py       # DocxConverter (Docling native XML + figure captions)
│   │   ├── xlsx.py       # XlsxConverter
│   │   ├── pptx.py       # PptxConverter (Docling + python-pptx + figure captions)
│   │   ├── image.py      # ImageConverter (rasterize page → --llm; used for images and --engine llm)
│   │   └── media.py      # MediaConverter (audio/video → --llm)
│   ├── markdown.py       # Shared markdown building + image catalog
│   ├── vlm.py            # Caption description pipelines (local HF + external API)
│   ├── providers.py      # External LLM provider config + parsing
│   ├── google_docs.py    # Google Docs/Sheets download
│   ├── formats.py        # Format detection, Engine enum, CaptionsSpec parsing
│   └── output.py         # Output dir helpers, cache check
├── config.py             # Settings (pydantic-settings)
├── media_llm.py          # Direct LLM client for audio/video (Gemini Files API + OpenRouter)
├── audio.py              # Audio recording (sox) + transcription/analysis prompts
├── video.py              # Video processing + YouTube download (yt-dlp) + prompts
├── logging_config.py     # Rich logging setup
├── tracing.py            # OpenTelemetry tracing
└── py.typed              # PEP 561 marker
```

## Conventions

- CLI framework: Typer (not argparse)
- HTTP client: httpx (not requests)
- Config: pydantic-settings (not os.environ)
- Logging: Rich + logging module (not print)
- Conversion paths:
  - PDF `--engine local` (default): Docling layout + OCR + tables; figures captioned via `BaseConverter.describe_figures` (honors `--captions`).
  - PDF `--engine llm`: rasterize each page → `--llm` (whole page markdown). Same code path as Image.
  - Image: always `--engine llm` (requires `--llm`).
  - DOCX / PPTX: native XML / python-pptx for body, `describe_figures` for picture captions.
  - XLSX: native, no LLM.
  - Audio / Video: `--llm` (default `google/gemini-3.1-pro-preview` via Gemini Files API, no size limit).
  - Document `--analyze` pass: `--llm` (default `ibm/claude-opus-4-8`, text-only, reasoning-focused; resolver: `providers.resolve_document_analysis_llm`).
  - Media `--analyze` pass: same multimodal model as the conversion (`resolve_media_llm`), since it sends the audio/video again.
- Figure captions are unified across PDF/DOCX/PPTX in `base.describe_figures`, dispatching on `CaptionsSpec` (CaptionsOff / CaptionsLocal / CaptionsLlm).
- Offline by default for local captioner: HF model pre-downloaded to `~/.cache/models` via `doc-convert --download-models [--captions <preset>]`.
- External LLM providers: `google/<model>` (GenAI), `openrouter/<model>`, `ibm/<model>` (OpenAI-compatible; `IBM_ICA_MODEL_KEY` + `IBM_ICA_BASE_URL`). IBM uses base64 inline for audio/video (no Files API).
- Cache: skip conversion if output exists, use `-f` to force.
- Unified output: all conversions write to `<name>_docling/document.md`.
- Apple Silicon: PDF pipeline and local captioner auto-retry on CPU when an MPS float64 error is raised (detection in `vlm.is_mps_float64_error`). `--cpu` is still available to force CPU from the start.
- Output guard (`doc_convert.output_guard`): every output dir is registered before mkdir; on any failure (exception, Ctrl+C, SIGTERM/SIGHUP/SIGQUIT, typer.Exit), `cleanup_pending()` runs in a `finally` block and removes the dir if it was newly created and contains no `document.md` or `audio.ogg`. Pre-existing dirs keep their original content; only files added by the failed run are removed. SIGKILL cannot be caught.

## Quality Gate

Run `make check` before every commit. It runs: lint, format-check, typecheck, security, test-cov.

## Coding Standards

This project follows the `python` skill. Reload it for full coding standards reference.
