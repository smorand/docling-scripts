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
doc-convert --download-models              # Pre-download default VLM (smolvlm)
doc-convert document.pdf                   # Convert a document
doc-convert --start-audio                  # Record + transcribe
doc-convert video.mp4                      # Extract video content
```

## Project Structure

```
src/
├── doc_convert.py    # Typer CLI entry point + conversion logic
├── config.py         # Settings (pydantic-settings)
├── media_llm.py      # Direct LLM client for audio/video (Gemini Files API + OpenRouter)
├── audio.py          # Audio recording (sox) + transcription/analysis prompts
├── video.py          # Video processing + YouTube download (yt-dlp) + prompts
├── logging_config.py # Rich logging setup
├── tracing.py        # OpenTelemetry tracing
└── py.typed          # PEP 561 marker
```

## Conventions

- CLI framework: Typer (not argparse)
- HTTP client: httpx (not requests)
- Config: pydantic-settings (not os.environ)
- Logging: Rich + logging module (not print)
- Six conversion paths: PDF local, PDF/Image external LLM, PPTX (Docling + python-pptx + VLM), DOCX (Docling + VLM images), XLSX native, Audio (transcription via external LLM), Video (extraction via external LLM)
- Offline by default for documents: VLM models pre-downloaded to ~/.cache/models
- Audio/video require external LLM (default: openrouter/google/gemini-2.5-flash)
- Cache: skip conversion if output exists, use -f to force
- Unified output: all conversions output to `<name>_docling/document.md`

## Quality Gate

Run `make check` before every commit. It runs: lint, format-check, typecheck, security, test-cov.

## Coding Standards

This project follows the `python` skill. Reload it for full coding standards reference.
