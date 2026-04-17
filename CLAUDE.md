# docling-scripts

## Overview

Unified document converter (PDF, images, DOCX, XLSX, PPTX, Google Docs/Sheets) powered by Docling.
Tech stack: Python 3.10+, Typer, Docling, python-pptx, httpx, pydantic-settings, Rich, OpenTelemetry.

## Key Commands

```
make sync          # Install dependencies
make run ARGS='convert document.pdf'  # Run via uv
make check         # Full quality gate (lint, format, typecheck, security, tests)
make install       # Install as uv tool (system-wide: doc-convert)
make uninstall     # Remove uv tool
doc-convert download-models              # Pre-download default VLM (granite_vision)
doc-convert convert document.pdf         # Convert a document
```

## Project Structure

```
src/
├── doc_convert.py    # Typer CLI entry point + conversion logic
├── config.py         # Settings (pydantic-settings)
├── logging_config.py # Rich logging setup
├── tracing.py        # OpenTelemetry tracing
└── py.typed          # PEP 561 marker
```

## Conventions

- CLI framework: Typer (not argparse)
- HTTP client: httpx (not requests)
- Config: pydantic-settings (not os.environ)
- Logging: Rich + logging module (not print)
- Four conversion paths: PDF local (StandardPdfPipeline), PDF/Image Gemini (VlmPipeline), PPTX (Docling native + python-pptx images + VLM), DOCX/XLSX native
- Offline by default: VLM models pre-downloaded to ~/.cache/models (MODELS_PATH env var), no network calls at runtime

## Quality Gate

Run `make check` before every commit. It runs: lint, format-check, typecheck, security, test-cov.

## Coding Standards

This project follows the `python` skill. Reload it for full coding standards reference.
