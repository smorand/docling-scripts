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

All conversions output to a `<name>_docling/` directory with `document.md` as the main file.

```bash
# PDF: full local extraction (figures, tables, OCR, page annotations)
doc-convert document.pdf
doc-convert document.pdf --no-vlm --no-figures          # text + tables only, faster
doc-convert document.pdf --all                          # + html, json, txt exports

# PDF via external LLM (quick markdown, no figure extraction)
doc-convert document.pdf --use-external-llm google/gemini-2.5-flash

# DOCX: native text + image extraction with VLM descriptions
doc-convert document.docx
doc-convert document.docx --no-figures                  # text only

# PPTX: native text + image extraction with VLM descriptions
doc-convert slides.pptx

# XLSX
doc-convert spreadsheet.xlsx

# Audio: transcription (requires external LLM, auto: google/gemini-3.1-pro-preview)
doc-convert meeting.ogg                                 # → meeting_docling/document.md
doc-convert meeting.ogg --analyze                       # + analysis.md
doc-convert meeting.ogg --analyze -i "Focus on action items only"

# Live recording from microphone
doc-convert --start-audio "Weekly Standup"              # → Weekly Standup_docling/audio.ogg + document.md
doc-convert --start-audio "One 2 One" --analyze         # + analysis.md

# Video: content extraction (requires external LLM)
doc-convert video.mp4                                   # → video_docling/document.md
doc-convert video.mp4 --analyze                         # + analysis.md
doc-convert "https://youtube.com/watch?v=..."           # YouTube via yt-dlp

# Images (requires external LLM)
doc-convert scan.png --use-external-llm google/gemini-2.5-flash

# Google Docs / Sheets
doc-convert "https://docs.google.com/document/d/DOC_ID/edit"

# Cache: skip if output exists, use -f to force
doc-convert document.pdf                                # skips if _docling/ exists
doc-convert document.pdf -f                             # force re-conversion
doc-convert document.pdf -o /custom/output              # override output directory
```

### Output Structure

```
<name>_docling/
├── document.md    # Main file (always present)
├── images.md      # Image catalog (PDF, DOCX, PPTX with figures)
├── figures/       # Extracted images (PDF, DOCX, PPTX with figures)
├── analysis.md    # Analysis (audio/video with --analyze)
├── audio.ogg      # Recording (--start-audio only)
└── output.*       # Additional formats (--all: md, html, json, txt)
```

### External LLM Providers

`--use-external-llm` supports three providers:

| Provider | Format | API Key | Extra |
|---|---|---|---|
| Google GenAI | `google/<model>` | `GOOGLE_API_KEY` | |
| OpenRouter | `openrouter/<model>` | `OPENROUTER_API_KEY` | |
| IBM ICA (OpenAI-compatible) | `ibm/<model>` | `IBM_ICA_MODEL_KEY` | requires `IBM_ICA_BASE_URL` |

Audio and video default to `google/gemini-3.1-pro-preview` if `--use-external-llm` is not specified. The `google/` provider uploads media via the Gemini Files API, so there is no payload size limit.

For IBM ICA and OpenRouter, audio/video files are sent inline (base64) via the chat completions endpoint; the Gemini Files API is not used. Large media will fail with a 502, so prefer the `google/` provider (the default) for recordings.

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
| `IBM_ICA_MODEL_KEY` | `ibm/` provider | IBM ICA API key (OpenAI-compatible) |
| `IBM_ICA_BASE_URL` | `ibm/` provider | IBM ICA base URL (e.g. `https://api.nextgen-beta.ica.ibm.com/ica/v1`) |
| `GOOGLE_CREDENTIALS` | Google Docs/Sheets URLs | Path to Google credentials JSON |

## Development

```bash
make sync          # Install dependencies (with local docling override)
make check         # Full quality gate before committing
make install       # Install system-wide via uv tool
make uninstall     # Remove system-wide install
```