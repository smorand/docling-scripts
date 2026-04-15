# docling-scripts

## Overview

Unified document converter (PDF, images, DOCX, XLSX, Google Docs/Sheets) powered by Docling.
Tech stack: Python 3.10+, Typer, Docling, httpx, pydantic-settings, Rich, OpenTelemetry.

## Key Commands

```
make sync          # Install dependencies
make run ARGS='document.pdf'  # Run via uv
make check         # Full quality gate (lint, format, typecheck, security, tests)
make install       # Install as uv tool (system-wide: doc-convert)
make uninstall     # Remove uv tool
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
- Three conversion paths: PDF local (StandardPdfPipeline), PDF/Image Gemini (VlmPipeline), DOCX/XLSX native

## Quality Gate

Run `make check` before every commit. It runs: lint, format-check, typecheck, security, test-cov.

## Coding Standards

This project follows the `python` skill. Reload it for full coding standards reference.
