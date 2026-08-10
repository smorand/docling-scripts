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

### The flags that matter

- `--llm <provider/model>` — remote model identity. Used for captions, document analysis, PDF/image `--engine llm`, and as fallback for media when `--media-llm` is not given. For image inputs only, if omitted, `doc-convert` now defaults to `ibm/claude-sonnet-4-5`.
- `--media-llm <provider/model>` — overrides the LLM used for audio/video conversion and media analysis only. Precedence: `--media-llm` > `--llm` > a per-type default. **Audio defaults to `ibm/gemini-3.1-pro-preview`**: audio is normalised to a compact mono 16 kHz OGG Opus copy and split with overlap when needed (see below), so it fits the inline payload limit and runs on the single IBM credential. **Video defaults to `google/gemini-3.1-pro-preview`**: it uploads via the Gemini Files API (no size limit), which large video needs since `ibm/` and `openrouter/` send inline base64.
- `--captions <value>` — figure captioner. Value is `off`, a local preset (`smolvlm`, `granite_vision`, `pixtral`, `qwen`), or a `provider/model` slug. Defaults: same as `--llm` if set, else `ibm/claude-sonnet-4-5` if `IBM_ICA_MODEL_KEY` + `IBM_ICA_BASE_URL` are configured, else local `smolvlm`.
- `--engine local|llm` — PDF/image body extraction. `local` (default) uses Docling layout + OCR + tables. `llm` rasterizes each page and sends it to `--llm` (whole document is described by the model). Images always use `llm`, and now default to `ibm/claude-sonnet-4-5` when `--llm` is omitted.
- `--ocr-model <value>` — OCR engine for the `--engine local` PDF pipeline. OCR is the per-region "text from bitmap" stage; it only fires on scanned/image content, so born-digital PDFs are unaffected (and incur no LLM cost even with the LLM default). Value is `off` (alias for `--no-ocr`), `local` (Tesseract CLI via the system `tesseract` binary), or a `provider/model` slug to read each image region with a cloud LLM. **Default: `ibm/gemini-3.1-pro-preview`** (IBM ICA fronts Gemini, so no separate `GOOGLE_API_KEY` is needed; per-region crops go inline, so the missing Files API on `ibm/` is irrelevant here). Unlike `--captions`, it does **not** auto-inherit `--llm`. For fully-scanned documents prefer `--engine llm` (reads whole pages); LLM OCR shines on mostly-digital docs with small embedded image-text. The `local` engine needs the `tesseract` binary, plus language packs (`brew install tesseract-lang`) for anything beyond the bundled `eng`.
- `--no-caption-filter` — turns off the caption filter, which is **on by default** and skips figures that cannot carry information before any captioner is paid. Two stages: a native size floor (anything under 64px on either axis) and docling's own figure classifier, which drops confidently decorative artwork (`logo`, `icon`, `qr_code`, `bar_code`, `stamp`, `signature` at ≥0.8 confidence). Skipped figures disappear from `document.md` and `images.md`; their PNG stays in `figures/` so you can audit what was filtered. On real client decks this removes 25-56% of caption calls (corporate and vendor logos, pictograms) with no content loss. Everything fails open: an unreadable image, a missing model, or a hesitant classification means "caption it anyway". See [Caption filter](#caption-filter).

### Caption filter

Slide decks are full of artwork that costs a caption call and returns nothing: the company logo on every slide, partner logos, arrow and gear pictograms. Exact-duplicate images were already deduplicated by content hash, but the same logo re-exported at a slightly different size is a *different* file, so hashing never caught it.

The filter runs before captioning, cheapest and most certain signal first:

1. **Size floor (free, no model).** A figure whose native resolution is under 64px on either axis cannot hold a legible chart or photo. This is an information-capacity fact, not a guess. Transparency is deliberately *not* used as a signal: plenty of real content is exported with an alpha channel.
2. **Figure classification (~7 ms/image on Apple Silicon, batched).** Reuses `docling-project/DocumentFigureClassifier-v2.5`, the EfficientNet-B0 classifier docling already ships and caches, so there is no extra dependency and no extra download. It labels each figure across 26 document categories; only the decorative ones above the confidence threshold are dropped, and the catch-all `other` class never is.

The model is lazy-loaded on first use. `doc-convert --download-models` pre-fetches it for machines that will run offline later, but that is optional: if the model is unavailable the filter logs one warning and continues with the size floor alone.

### Batch (multiple files)

Pass more than one file (shell globs work) and each is converted independently, in its own `doc-convert` subprocess. Subprocess isolation means one failed file never aborts the batch, and there is no shared torch/MPS or model-load contention.

```bash
# Sequential (default): each file streamed live, one after another
doc-convert *.ogg

# Parallel: -P N runs N files at a time
doc-convert *.ogg -P 4

# Bare -P (or -P 0) uses one worker per CPU core
doc-convert *.pdf -P

# Any per-file flags apply to every file
doc-convert *.ogg -P 4 --analyze --lang fr
```

- `-P/--parallel N` — number of files converted concurrently. `1` (default) is sequential; `0` or a bare `-P` means all cores. Ignored for a single file.
- Parallel mode (`-P >1`) captures each file's output and prints it as a block when that file finishes, followed by a summary (`N ok, M failed`). Sequential mode streams output live.
- Exit code is `0` only if every file succeeded, else `1`. Ctrl+C stops the batch; each child cleans up its own partial output.
- Not combinable with `--start-audio`, `--download-models`, `--download-enrichments`, `-o/--output`, or `--stdout` (all single-target modes).
- Per-file caching still applies: re-running a batch skips files already converted (use `-f` to force).

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
doc-convert document.pdf --no-ocr        # same as --ocr-model off

# Pick the OCR engine for scanned/image regions (local pipeline keeps figures + tables)
doc-convert document.pdf                                    # default: Gemini reads scanned regions
doc-convert document.pdf --ocr-model local                  # offline Tesseract CLI
doc-convert document.pdf --ocr-model ibm/claude-haiku-4-5   # a different cloud LLM

# Apple Silicon: --cpu forces CPU from the start. If omitted, the PDF pipeline and
# local captioner auto-fall back to CPU when an MPS float64 error is raised.
doc-convert document.pdf --cpu

# All output formats (md, html, json, txt)
doc-convert document.pdf --all
```

### DOCX

```bash
# Native XML parse + figure captions (auto-routes to Gemini Flash Lite if GOOGLE_API_KEY is set)
doc-convert deck.docx

# Explicit external captioner
doc-convert deck.docx --llm openrouter/anthropic/claude-haiku-4.5

# Local captioner only, ignore any env auto-routing
doc-convert deck.docx --captions qwen

# Text only
doc-convert deck.docx --no-figures
doc-convert deck.docx --captions off
```

### PPTX

By default, PPTX conversion does more than a native text parse: **every slide
is also rendered as a real screenshot and visually interpreted by a vision
LLM**, because a slide's meaning often lives in its layout, diagrams, and
schemas, not just its raw text runs. `document.md` groups the output **per
slide**, each with 3 clearly labelled subsections so a downstream LLM can
tell them apart:

1. **Extracted Content (text + figures)** — the mechanical docling +
   python-pptx parse (same as DOCX): text runs, tables, and extracted
   embedded images with their VLM figure captions.
2. **Visual Interpretation (full slide screenshot)** — a detailed
   interpretation of the slide exactly as it renders (layout, visual
   hierarchy, what any chart/diagram/schema actually shows), produced by a
   vision LLM from a full-slide screenshot. This is independent from step 1;
   the two are not merged, so you can compare "what was extracted" against
   "what the slide looks like".
3. **Speaker Notes** — the presenter notes attached to the slide (if any).

Slide screenshots are rendered faithfully via headless LibreOffice
(`pptx → pdf`) then rasterized page-by-page, landing in `<output>/slides/`.

```bash
# Default: native text/figures + whole-slide screenshot interpretation (ibm/claude-sonnet-4-6)
doc-convert slides.pptx

# Different vision model for the slide screenshot pass
doc-convert slides.pptx --slide-vlm openrouter/anthropic/claude-haiku-4.5

# Figure captioner still configurable independently (step 1 only)
doc-convert slides.pptx --llm ibm/gemini-3.1-pro-preview
doc-convert slides.pptx --captions qwen

# Faster/cheaper: skip the screenshot pass entirely (flat document.md, like DOCX)
doc-convert slides.pptx --no-slide-screenshots

# Text only, no figures, no screenshots
doc-convert slides.pptx --no-figures --no-slide-screenshots
```

Requires the `soffice` (LibreOffice) binary for rendering; see System
Dependencies below.

### XLSX

```bash
doc-convert spreadsheet.xlsx
```

### Image (always engine=llm)

```bash
doc-convert scan.png

doc-convert scan.png --llm google/gemini-3.1-pro-preview
```

If `--llm` is omitted for a direct image input, `doc-convert` now uses `ibm/claude-sonnet-4-5` by default.

### Audio

```bash
# Transcription with speaker diarization (defaults to ibm/gemini-3.1-pro-preview)
# Any audio format works, incl. .opus; it is normalised to a compact ogg first.
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

The email body is rendered with `html2text` after a cleanup pass (tracking
pixels, spacer gifs and empty tracking anchors are removed). Link URLs are
preserved and the layout tables that marketing/HR emails are built from are
flattened into readable paragraphs. Remote images are never downloaded, so
opening a converted email does not trigger any open-tracking beacon.

### Cache and output

```bash
doc-convert document.pdf                  # skips if <name>_docling/document.md exists
doc-convert document.pdf -f               # force re-conversion
doc-convert document.pdf -o /custom/dir   # override output directory
doc-convert document.pdf --stdout         # also print document.md to stdout (logs stay on stderr)
doc-convert document.pdf --stdout | glow  # pipe straight into another tool
```

`--stdout` prints the final `document.md` on process stdout once the conversion (and any
`--analyze`/`--note` steps) finishes; it works with cached runs too. All logging already goes to
stderr, so stdout stays clean for piping. Not combinable with multiple input files.

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
├── slides/                 # Whole-slide screenshots (PPTX, default; slide_NNN.png)
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

Media defaults depend on the type when `--media-llm`/`--llm` is not given: **audio → `ibm/gemini-3.1-pro-preview`**, **video → `google/gemini-3.1-pro-preview`**.

Audio is first normalised to a compact mono 16 kHz OGG Opus copy (~14 MB/h) and, for inline providers (`ibm/`, `openrouter/`), split into 1-minute-overlapping parts when it would exceed the inline payload limit (~50 MB), so it always fits and can run on the single IBM credential. Video uses the `google/` Gemini Files API (upload then generate), which has no payload size limit — needed because `ibm/` and `openrouter/` send media inline (base64) and large video would fail with a 502.

## System Dependencies

| Tool | Required for | Install |
|---|---|---|
| sox | `--start-audio` (recording) | `brew install sox` |
| yt-dlp | YouTube URLs | `brew install yt-dlp` |
| ffmpeg + ffprobe | Video > 30 min chunking | `brew install ffmpeg` |
| soffice (LibreOffice) | PPTX slide screenshots (default; disable with `--no-slide-screenshots`) | `brew install libreoffice` |

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
