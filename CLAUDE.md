# Docling Scripts

Standalone CLI scripts for document conversion using the [Docling](https://github.com/DS4SD/docling) library.

## Scripts

| Script | Command | Description |
|---|---|---|
| `doc_to_md.py` | `doc-to-md` | Convert PDF, images, DOCX, XLSX, Google Docs/Sheets to markdown (uses Gemini VLM for PDF/images) |
| `convert_pdf.py` | `convert-pdf` | Convert PDF to page-annotated markdown with extracted figures (uses local VLM models) |
| `docling_convert_pdf.sh` | — | Batch wrapper around `convert_pdf.py` for multiple PDFs |

## How It Works

### Local Docling Dependency

This project depends on a local checkout of docling at `~/projects/external/docling`. Configured in `pyproject.toml`:

```toml
[tool.uv.sources]
docling = { path = "/Users/sebastien/projects/external/docling", editable = true }
```

Run `uv sync` to install all dependencies.

### PEP 723 Inline Metadata (Standalone Execution)

Each Python script includes **PEP 723 inline metadata** at the top:

```python
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "docling @ file:///Users/sebastien/projects/external/docling",
#     "requests",
# ]
# ///
```

This allows running scripts from anywhere with `uv run /full/path/to/script.py` — uv reads dependencies from the script itself, no `cd` or `pyproject.toml` lookup needed.

### System-Wide Installation

Wrapper `.sh` scripts are installed in `~/.local/bin/`:

```
~/.local/bin/doc-to-md    → exec uv run .../doc_to_md.py "$@"
~/.local/bin/convert-pdf  → exec uv run .../convert_pdf.py "$@"
```

To reinstall after changes:
```bash
cp doc-to-md.sh ~/.local/bin/doc-to-md
cp convert-pdf.sh ~/.local/bin/convert-pdf
chmod +x ~/.local/bin/doc-to-md ~/.local/bin/convert-pdf
```

## Adding a New Script

1. Create `my_script.py` with a `main()` function
2. Add PEP 723 inline metadata block after the shebang, listing all dependencies (use `docling @ file:///Users/sebastien/projects/external/docling` for local docling)
3. Add dependencies to `pyproject.toml` as well
4. Add entry point in `[project.scripts]`
5. Create `my-script.sh` wrapper: `exec uv run /Users/sebastien/Documents/Projects/docling-scripts/my_script.py "$@"`
6. Copy wrapper to `~/.local/bin/` and `chmod +x`

## Environment Variables

| Variable | Used By | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | `doc_to_md.py` | Gemini API key for PDF/image VLM conversion |
| `GOOGLE_CREDENTIALS` | `doc_to_md.py` | Path to Google service account or authorized user JSON (for Google Docs/Sheets) |
