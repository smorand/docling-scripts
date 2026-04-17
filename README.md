# docling-scripts

Unified document converter powered by [Docling](https://github.com/docling-project/docling).

Converts PDF, images, DOCX, XLSX, PPTX, audio, and video to structured markdown.

## Installation

```bash
git clone https://github.com/smorand/docling-scripts.git
cd docling-scripts
make install    # Installs doc-convert as isolated uv tool in ~/.local/bin/
```

## Model Setup (offline, for document VLM)

```bash
doc-convert --download-models                           # download smolvlm (default)
doc-convert --download-models --vlm-preset granite_vision  # specific preset
```

Models are stored in `~/.cache/models/` (override with `MODELS_PATH` env var).

## Usage

```bash
# PDF: full local extraction (figures, tables, OCR, page annotations)
doc-convert document.pdf                                # output to <stem>_docling/
doc-convert document.pdf -o /tmp/output                 # custom output directory
doc-convert document.pdf --no-vlm                       # skip picture descriptions
doc-convert document.pdf --all                          # + html, json, txt exports

# PDF via external LLM (quick markdown, no figure extraction)
doc-convert document.pdf --use-external-llm google/gemini-2.5-flash

# PPTX: native text + image extraction with VLM descriptions
doc-convert slides.pptx                                 # output to <stem>_docling/
doc-convert slides.pptx --use-external-llm openrouter/google/gemini-2.5-flash
doc-convert slides.pptx --no-vlm                        # skip image descriptions

# Audio: transcription (requires external LLM, default: openrouter/google/gemini-2.5-flash)
doc-convert meeting.ogg                                 # transcription to <stem>_transcription.md
doc-convert meeting.ogg --analyze                       # + structured analysis
doc-convert meeting.ogg -m "Weekly standup"             # with meeting context
doc-convert --start-audio                               # record from mic, Ctrl+C to transcribe
doc-convert --start-audio --analyze -m "Standup"        # record + transcribe + analyze

# Video: content extraction (requires external LLM)
doc-convert video.mp4                                   # extraction to <stem>_extraction.md
doc-convert video.mp4 --analyze                         # + executive summary
doc-convert "https://youtube.com/watch?v=..."           # YouTube via yt-dlp

# Images (requires external LLM)
doc-convert scan.png --use-external-llm google/gemini-2.5-flash

# DOCX / XLSX (native parsers)
doc-convert document.docx                               # output to stdout
doc-convert document.docx -o out.md                     # output to file

# Google Docs / Sheets
doc-convert "https://docs.google.com/document/d/DOC_ID/edit"

# Cache: skip conversion if output exists (use -f to force)
doc-convert document.pdf                                # skips if output exists
doc-convert document.pdf -f                             # force re-conversion
```

### External LLM Providers

`--use-external-llm` supports two providers:

| Provider | Format | API Key |
|---|---|---|
| Google GenAI | `google/<model>` | `GOOGLE_API_KEY` |
| OpenRouter | `openrouter/<model>` | `OPENROUTER_API_KEY` |

Audio and video default to `openrouter/google/gemini-2.5-flash` if `--use-external-llm` is not specified.

### System Dependencies

| Tool | Required for | Install |
|---|---|---|
| sox | `--start-audio` (recording) | `brew install sox` |
| yt-dlp | YouTube URLs | `brew install yt-dlp` |

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
