# docling-scripts

Unified document converter powered by [Docling](https://github.com/docling-project/docling).

Converts PDF, images, DOCX, XLSX, and Google Docs/Sheets to structured markdown.

## Installation

```bash
git clone https://github.com/smorand/docling-scripts.git
cd docling-scripts
make install    # Installs doc-convert as isolated uv tool in ~/.local/bin/
```

## Usage

```bash
# PDF: full local extraction (figures, tables, OCR, page annotations)
doc-convert document.pdf                       # output to <stem>_docling/
doc-convert document.pdf -o /tmp/output        # custom output directory
doc-convert document.pdf --no-vlm              # skip picture descriptions
doc-convert document.pdf --vlm-preset granite_vision
doc-convert document.pdf --all                 # + html, json, txt exports

# PDF via Gemini API (quick markdown, no figure extraction)
doc-convert document.pdf --gemini              # output to stdout
doc-convert document.pdf --gemini -o out.md    # output to file

# Images (uses Gemini VLM)
doc-convert scan.png                           # output to stdout
doc-convert scan.png -O                        # output to <stem>.md

# DOCX / XLSX (native parsers)
doc-convert document.docx                      # output to stdout
doc-convert document.docx -o out.md            # output to file

# Google Docs / Sheets
doc-convert "https://docs.google.com/document/d/DOC_ID/edit"
doc-convert "https://docs.google.com/spreadsheets/d/SHEET_ID/edit"
```

### PDF Local Output Structure

```
document_docling/
├── document.md    # Page-annotated markdown with metadata
├── images.md      # Image catalog (type, caption, VLM description)
└── figures/       # Extracted images as PNG
```

With `--all`, additional exports: `output.md`, `output_embedded.md`, `output.html`, `output.json`, `output.txt`.

## Environment Variables

| Variable | Required for | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | `--gemini`, image conversion | Gemini API key |
| `GOOGLE_CREDENTIALS` | Google Docs/Sheets URLs | Path to Google credentials JSON |

## Development

```bash
make sync          # Install dependencies (with local docling override)
make check         # Full quality gate before committing
make install       # Install system-wide via uv tool
make uninstall     # Remove system-wide install
```
