# docling-scripts

Unified document converter powered by [Docling](https://github.com/docling-project/docling).

Converts PDF, images, DOCX, XLSX, PPTX, audio, and video to structured markdown.

## Installation

```bash
git clone https://github.com/smorand/docling-scripts.git
cd docling-scripts
make install    # Installs doc-convert as isolated uv tool in ~/.local/bin/
```

## Model Setup (offline, local captioner)

```bash
doc-convert --download-models                          # download smolvlm (default)
doc-convert --download-models --captions qwen          # specific preset
```

Models are stored in `~/.cache/models/` (override with `MODELS_PATH` env var).
Local captioner presets: `smolvlm`, `granite_vision`, `pixtral`, `qwen`.

## Usage

All conversions write to a `<name>_docling/` directory with `document.md` as the main file.

### The three flags that matter

- `--llm <provider/model>` — remote model identity. Used for captions, document analysis, PDF/image `--engine llm`, and as fallback for media when `--media-llm` is not given.
- `--media-llm <provider/model>` — overrides the LLM used for audio/video conversion and media analysis only. Precedence: `--media-llm` > `--llm` > `google/gemini-3.1-pro-preview` (default uses Gemini Files API; required for big recordings since `ibm/` and `openrouter/` send inline base64 and fail on large media).
- `--captions <value>` — figure captioner. Value is `off`, a local preset (`smolvlm`, `granite_vision`, `pixtral`, `qwen`), or a `provider/model` slug. Defaults: same as `--llm` if set, else `ibm/claude-haiku-4-5` if `IBM_ICA_MODEL_KEY` + `IBM_ICA_BASE_URL` are configured, else `google/gemini-3.1-flash-lite-preview` if `GOOGLE_API_KEY` is in env, else local `smolvlm`.
- `--engine local|llm` — PDF/image body extraction. `local` (default) uses Docling layout + OCR + tables. `llm` rasterizes each page and sends it to `--llm` (whole document is described by the model). Images always use `llm`.

### PDF

```bash
# Default: local pipeline, local smolvlm captions
doc-convert document.pdf

# Local pipeline + heavier local captioner
doc-convert document.pdf --captions qwen

# Hybrid: local text + tables + external figure captions (the new headline mode)
doc-convert document.pdf --llm openrouter/anthropic/claude-haiku-4.5
doc-convert document.pdf --captions google/gemini-3.1-pro-preview

# Full-page rasterization through an external LLM
doc-convert document.pdf --llm google/gemini-3.1-pro-preview --engine llm

# Different models for body vs figure captions
doc-convert document.pdf --llm google/gemini-3.1-pro-preview \
                         --captions openrouter/anthropic/claude-haiku-4.5

# Text + tables only, no figures
doc-convert document.pdf --no-figures

# Local pipeline, no figure descriptions
doc-convert document.pdf --captions off

# Disable OCR (local pipeline only)
doc-convert document.pdf --no-ocr

# Apple Silicon: --cpu forces CPU from the start. If omitted, the PDF pipeline and
# local captioner auto-fall back to CPU when an MPS float64 error is raised.
doc-convert document.pdf --cpu

# All output formats (md, html, json, txt)
doc-convert document.pdf --all
```

### DOCX / PPTX

```bash
# Native XML parse + figure captions (auto-routes to Gemini Flash Lite if GOOGLE_API_KEY is set)
doc-convert deck.docx
doc-convert slides.pptx

# Explicit external captioner
doc-convert deck.docx --llm openrouter/anthropic/claude-haiku-4.5
doc-convert slides.pptx --llm ibm/gemini-3-pro-preview

# Local captioner only, ignore any env auto-routing
doc-convert deck.docx --captions qwen

# Text only
doc-convert deck.docx --no-figures
doc-convert deck.docx --captions off
```

### XLSX

```bash
doc-convert spreadsheet.xlsx
```

### Image (always engine=llm)

```bash
doc-convert scan.png --llm google/gemini-3.1-pro-preview
```

### Audio

```bash
# Transcription with speaker diarization (defaults to google/gemini-3.1-pro-preview)
doc-convert meeting.ogg

# Transcription + text analysis on the consolidated document.md
doc-convert meeting.ogg --analyze
doc-convert meeting.ogg --analyze -m "Steering committee" --lang fr
doc-convert meeting.ogg --analyze --analyze-prompt "Focus on action items only"

# Use a different model for the analysis pass only
doc-convert meeting.ogg --analyze --analyze-model ibm/claude-opus-4-8

# Audio-only: produce an HTML meeting brief, opened in browser
doc-convert meeting.ogg --meeting-summary
doc-convert meeting.ogg --meeting-summary --analyze

# Companion .md alongside meeting.ogg (e.g. meeting.md) → adds
# ## Additional Context, ## Screenshots (with VLM descriptions),
# ## Additional Documents (recursively converted) to document.md.

# Live recording from microphone
doc-convert --start-audio "Weekly Standup"
doc-convert --start-audio "One 2 One" --analyze --meeting-summary
```

### Video

```bash
doc-convert video.mp4
doc-convert video.mp4 --analyze
doc-convert "https://youtube.com/watch?v=..."
# Videos > 30 min are chunked via ffmpeg and produce an Executive Summary
# followed by per-chunk sections.
```

### Email (EML, MSG) and Google Docs / Sheets

```bash
doc-convert "https://docs.google.com/document/d/DOC_ID/edit"
doc-convert email.eml --analyze
doc-convert outlook_message.msg --analyze
# Attachments land under attachments/<file> with sub-folders
# attachments/<file>_docling/ for each recursively converted item,
# and a ## Attachments section is appended to document.md.
```

### Cache and output

```bash
doc-convert document.pdf                  # skips if <name>_docling/document.md exists
doc-convert document.pdf -f               # force re-conversion
doc-convert document.pdf -o /custom/dir   # override output directory
```

### Notes

```bash
doc-convert invoice.pdf --note            # store as a structured note
doc-convert invoice.pdf --note --note-force
```

## Output Structure

```
<name>_docling/
├── document.md             # Main file (always present, self-contained)
├── images.md               # Image catalog (PDF, DOCX, PPTX)
├── figures/                # Extracted figures (PDF, DOCX, PPTX)
├── tables/                 # Rendered tables fed to the captioner (PDF)
├── analyze.md              # Analysis (--analyze)
├── summary.html            # Meeting brief (--meeting-summary, audio only)
├── attachments/            # Email PJs (binary) + <name>_docling/ sub-folders
├── screenshots/            # Audio companion images (described in document.md)
├── additional_documents/   # Audio companion text docs + <name>_docling/ sub-folders
├── audio.ogg               # Recording (--start-audio only)
└── output.*                # Additional formats (--all: md, html, json, txt)
```

## External LLM Providers

`--llm` (and `--captions` when given a slug) accepts:

| Provider | Format | API Key | Extra |
|---|---|---|---|
| Google GenAI | `google/<model>` | `GOOGLE_API_KEY` | |
| OpenRouter | `openrouter/<model>` | `OPENROUTER_API_KEY` | |
| IBM ICA (OpenAI-compatible) | `ibm/<model>` | `IBM_ICA_MODEL_KEY` | requires `IBM_ICA_BASE_URL` |

Audio and video default to `google/gemini-3.1-pro-preview` if `--llm` is not given. The `google/` provider uploads media via the Gemini Files API, so there is no payload size limit.

For IBM ICA and OpenRouter, audio/video files are sent inline (base64) via the chat completions endpoint; the Gemini Files API is not used. Large media will fail with a 502, so prefer the `google/` provider for recordings.

## System Dependencies

| Tool | Required for | Install |
|---|---|---|
| sox | `--start-audio` (recording) | `brew install sox` |
| yt-dlp | YouTube URLs | `brew install yt-dlp` |
| ffmpeg + ffprobe | Video > 30 min chunking | `brew install ffmpeg` |

## Environment Variables

| Variable | Required for | Purpose |
|---|---|---|
| `MODELS_PATH` | Local captioner | Model cache directory (default: `~/.cache/models`) |
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
