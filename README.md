# docling-scripts

Unified document converter powered by [Docling](https://github.com/docling-project/docling).

Converts PDF, images, DOCX, XLSX, PPTX, and Google Docs/Sheets to structured markdown.

## Installation

```bash
git clone https://github.com/smorand/docling-scripts.git
cd docling-scripts
make install    # Installs doc-convert as isolated uv tool in ~/.local/bin/
```

## Model Setup (offline)

VLM models must be pre-downloaded before use. No network calls at runtime.

```bash
# Download default model (smolvlm, ~256M params)
doc-convert download-models

# Download a specific preset
doc-convert download-models granite_vision
doc-convert download-models pixtral
doc-convert download-models qwen

# Custom models directory (default: ~/.cache/models)
doc-convert download-models --models-path /path/to/models
```

Models are stored in `~/.cache/models/` (override with `MODELS_PATH` env var).

## Usage

```bash
# PDF: full local extraction (figures, tables, OCR, page annotations)
doc-convert convert document.pdf                       # output to <stem>_docling/
doc-convert convert document.pdf -o /tmp/output        # custom output directory
doc-convert convert document.pdf --no-vlm              # skip picture descriptions
doc-convert convert document.pdf --vlm-preset granite_vision
doc-convert convert document.pdf --all                 # + html, json, txt exports

# PDF via external LLM (quick markdown, no figure extraction)
doc-convert convert document.pdf --use-external-llm google/gemini-3-flash-preview
doc-convert convert document.pdf --use-external-llm openrouter/google/gemini-3-pro-preview

# PPTX: native text + image extraction with VLM descriptions
doc-convert convert slides.pptx                        # output to <stem>_docling/
doc-convert convert slides.pptx --use-external-llm google/gemini-3-flash-preview
doc-convert convert slides.pptx --no-vlm               # skip image descriptions
doc-convert convert slides.pptx --no-figures            # text + tables only

# Images (requires external LLM)
doc-convert convert scan.png --use-external-llm google/gemini-3-flash-preview
doc-convert convert scan.png --use-external-llm openrouter/google/gemini-3-pro-preview -O

# DOCX / XLSX (native parsers)
doc-convert convert document.docx                      # output to stdout
doc-convert convert document.docx -o out.md            # output to file

# Google Docs / Sheets
doc-convert convert "https://docs.google.com/document/d/DOC_ID/edit"
doc-convert convert "https://docs.google.com/spreadsheets/d/SHEET_ID/edit"
```

### External LLM Providers

`--use-external-llm` supports two providers:

| Provider | Format | API Key |
|---|---|---|
| Google GenAI | `google/<model>` | `GOOGLE_API_KEY` |
| OpenRouter | `openrouter/<model>` | `OPENROUTER_API_KEY` |

Examples: `google/gemini-3-flash-preview`, `openrouter/google/gemini-3-pro-preview`, `openrouter/anthropic/claude-sonnet-4`

### PDF/PPTX Output Structure

```
document_docling/
├── document.md    # Page/slide-annotated markdown with metadata
├── images.md      # Image catalog (type, caption, VLM description)
└── figures/       # Extracted images as PNG
```

With `--all`, additional exports: `output.md`, `output_embedded.md`, `output.html`, `output.json`, `output.txt`.

### PPTX Conversion

PPTX uses a hybrid approach:
- **Docling native** for structured text, tables, and lists
- **python-pptx** for robust image extraction (with recursion into grouped shapes)
- **Local VLM** (smolvlm by default) for image descriptions (offline)
- **External LLM** (`--use-external-llm`) as alternative for image descriptions

Note: Charts and SmartArt are not extracted (they require a rendering engine). For chart-heavy presentations, consider converting to PDF first via LibreOffice.

## Environment Variables

| Variable | Required for | Purpose |
|---|---|---|
| `MODELS_PATH` | Local VLM | Model cache directory (default: `~/.cache/models`) |
| `GOOGLE_API_KEY` | `google/` provider | Google GenAI API key |
| `OPENROUTER_API_KEY` | `openrouter/` provider | OpenRouter API key |
| `GOOGLE_CREDENTIALS` | Google Docs/Sheets URLs | Path to Google credentials JSON |

## Development

```bash
make sync          # Install dependencies (with local docling override)
make check         # Full quality gate before committing
make install       # Install system-wide via uv tool
make uninstall     # Remove system-wide install
```
