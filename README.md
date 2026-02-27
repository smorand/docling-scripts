# docling-scripts

Standalone CLI scripts for document conversion powered by [Docling](https://github.com/DS4SD/docling).

## Scripts

### doc-to-md

Convert PDF, images, DOCX, XLSX, or Google Docs/Sheets to structured markdown. Uses Gemini VLM for vision-based formats (PDF, images) and Docling's native parsers for DOCX/XLSX.

```bash
doc-to-md document.pdf                # output to stdout
doc-to-md document.pdf -o out.md      # output to file
doc-to-md document.pdf -O             # output to <stem>.md
doc-to-md document.docx               # native DOCX parser
doc-to-md scan.png -O                 # image via Gemini VLM
doc-to-md "https://docs.google.com/document/d/DOC_ID/edit"
```

Requires `GEMINI_API_KEY` env var for PDF/image conversion. Requires `GOOGLE_CREDENTIALS` for Google Docs/Sheets.

### convert-pdf

Convert PDF to page-annotated markdown with extracted figures using local VLM models.

```bash
convert-pdf input.pdf                          # default output to <stem>_docling/
convert-pdf input.pdf -o /tmp/my-output        # custom output directory
convert-pdf input.pdf --no-ocr                 # disable OCR
convert-pdf input.pdf --vlm-preset granite_vision
convert-pdf input.pdf --all                    # generate all formats (md, html, json, txt)
```

## Requirements

- [uv](https://docs.astral.sh/uv/) package manager
- A local clone of [Docling](https://github.com/DS4SD/docling) at `~/projects/external/docling`

## Installation

```bash
git clone https://github.com/smorand/docling-scripts.git
cd docling-scripts
uv sync

# Install system-wide commands
cp doc-to-md.sh ~/.local/bin/doc-to-md
cp convert-pdf.sh ~/.local/bin/convert-pdf
chmod +x ~/.local/bin/doc-to-md ~/.local/bin/convert-pdf
```

Each script also embeds [PEP 723](https://peps.python.org/pep-0723/) inline metadata, so you can run them directly from anywhere:

```bash
uv run /path/to/docling-scripts/doc_to_md.py document.pdf
```
