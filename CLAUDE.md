# docling-scripts

## Overview

Unified document converter (PDF, images, DOCX, XLSX, PPTX, audio, video, Google Docs/Sheets) powered by Docling.
Tech stack: Python 3.10+, Typer, Docling, python-pptx, httpx, pydantic-settings, Rich, OpenTelemetry.

## Key Commands

```
make sync          # Install dependencies
make run ARGS='document.pdf'  # Run via uv
make check         # Full quality gate (lint, format, typecheck, security, tests)
make install       # Install as uv tool (system-wide: doc-convert)
make uninstall     # Remove uv tool
doc-convert --download-models                                  # Pre-download default captioner (smolvlm)
doc-convert document.pdf                                       # Default: local pipeline, local captions
doc-convert document.pdf --llm openrouter/anthropic/claude-haiku-4.5   # Hybrid: local body + LLM captions
doc-convert document.pdf --llm google/gemini-3.1-pro-preview --engine llm   # Full-page rasterize via LLM
doc-convert photo.png                                          # Image conversion, defaults to ibm/gemini-3.7-flash
doc-convert photo.png --llm google/gemini-3.1-pro-preview      # Image conversion with explicit model
doc-convert email.msg                                          # Outlook MSG (via extract-msg) or .eml
doc-convert --start-audio "Meeting Name"                       # Record + transcribe (+ companion sections)
doc-convert video.mp4 --analyze                                # Extract + summarize
doc-convert meeting.ogg --meeting-summary                      # Audio only: HTML brief opened in browser
doc-convert *.ogg -P 4                                         # Batch: N files at a time (subprocess per file)
```

### Multi-file batch (`-P/--parallel`)

`document` is variadic (`documents: list[str]`). Passing >1 file routes to `doc_convert/batch.py:run_batch`, which converts **each file in its own `doc-convert` subprocess** (via `sys.argv[0]`, reusing the parent's flags with file tokens and `--parallel` stripped). Subprocess isolation is deliberate: `output_guard` is global/main-thread-only state and the PDF pipeline is torch/MPS, so threads would be unsafe. `-P N` = N concurrent workers; `0` or a bare `-P` = all cores (`os.cpu_count()`); `1` (default) = sequential with live streaming. Parallel mode captures per-file output, prints blocks + a summary; exit code `1` if any file failed. Bare `-P` (no value) is impossible in Typer, so `run()` (the console entry point, replacing `app`) normalises `sys.argv` (`-P` → `-P 0`) before Typer parses. A single file takes the unchanged in-process path (zero regression). Guards reject `-o`, `--start-audio`, `--download-models`, `--download-enrichments`, `--stdout` with >1 file.

`--stdout` prints the final `document.md` to stdout at every single-file exit point in `cli.py:_dispatch` (`_print_document_stdout`), after all steps (conversion, `--analyze`, `--note`) complete, including on a cached (skipped) conversion. All logging goes to stderr (`logging_config.py`), so stdout stays clean for piping (`doc-convert file.pdf --stdout | glow`).

### Flag surface (5 axes)

- `--llm <provider/model>` — remote model identity (captions, document analysis, `--engine llm`, and as fallback for media when `--media-llm` is not set). For direct image inputs only, omission now falls back to `ibm/gemini-3.7-flash`.
- `--media-llm <provider/model>` — overrides the LLM used for audio/video conversion + media analysis only. Precedence: `--media-llm` > `--llm` > per-type default. **Audio default = `ibm/gemini-3.7-flash`** (audio is normalised + split to fit the inline limit — see below — so it runs on the single IBM credential). **Video default = `google/gemini-3.7-flash`** (Files API; no size limit, needed for big video).
- `--captions <off|preset|provider/model>` — figure captioner. Defaults to `--llm` if set, else first cloud with creds from `AUTO_CAPTIONS_PREFERENCES` (today: `ibm/gemini-3.7-flash`), else local `smolvlm`. The auto-path is IBM-only (single credential); it no longer falls back to a `google/` model.
- `--engine local|llm` — PDF/image body parser. `local` (default for PDF) uses Docling; `llm` rasterizes pages to `--llm`. Images always `llm`; if `--llm` is omitted on a direct image input, the default is `ibm/gemini-3.7-flash`.
- `--ocr-model <off|local|provider/model>` — OCR engine for the `--engine local` PDF pipeline (the per-region text-from-bitmap stage; only fires on scanned/image content). Default = cloud LLM `DEFAULT_OCR_MODEL` (`ibm/gemini-3.7-flash`, via `ocr_llm.LlmOcrModel`; IBM ICA fronts Gemini so no separate `DOC_CONVERT_GOOGLE_API_KEY`, and per-region crops go inline so the missing Files API on `ibm/` is irrelevant); since OCR only fires on bitmap regions, born-digital PDFs cost nothing. `local` = Tesseract CLI (system binary; `tesseract-lang` packs needed for non-eng). Does NOT auto-inherit `--llm` (the OCR model is chosen by this axis alone). `--no-ocr` is an alias for `off`. Note: for fully-scanned docs `--engine llm` (whole-page) usually beats LLM OCR (which reads one region-blob at a time).
- `--slide-vlm <provider/model>` / `--no-slide-screenshots` — PPTX only. Controls the whole-slide screenshot visual-interpretation pass (see below). Precedence: `--slide-vlm` > `--llm` > default (`ibm/gemini-3.7-flash`). `--no-slide-screenshots` disables the pass and reverts PPTX to a flat text+figures `document.md` (same shape as DOCX).
- `--llm-concurrency N` — default `providers.DEFAULT_LLM_CONCURRENCY` (8). Governs **every** per-image vision call: figure/table captions (`vlm.describe_images_with_external_llm`) and PPTX slide screenshots (`pptx_slide_vlm.analyze_slide_images`). Both go through `vision_llm.map_concurrent`. `--slide-concurrency` is a hidden deprecated alias. Captions deliberately do **not** use docling's `VlmPipeline` any more: sending one figure to a chat endpoint is not a document conversion, and the detour cost a `DocumentConverter` per image (66 distinct option hashes over 100 builds, because the per-image context block is in the prompt and the prompt is in docling's cache key), forced sequential execution, and upscaled every figure 2x. Measured on a 98-slide/183-image deck: caption phase 914 s → 247 s, whole conversion 1211 s → 801 s, caption volume +0.7%, structure identical. The dropped 2x upscale cost up to +74% image tokens and 5.4x payload, and read *worse* (bar chart true value 904: native 900, upscale 950; the deck's only data table: native 40/39/28/20 correct, upscale 40/23/28/20). The `markdown → DoclingDocument → markdown` round trip it also removed was verified byte-identical on 99/99 captions. **The logged `Nx overlap` is overlap, not speedup**: per-call latency inflates under concurrency (captions 7.8 s sequential → 10.5 s median at 8), so the real caption gain there was 3.7x. `converters/image.convert_image_to_markdown` (whole image/PDF via `--engine llm`) still uses docling on purpose: rasterising a multi-page PDF into pages is exactly what that pipeline is for. `ocr_llm` was moved off it after its own measurement (see below). **Benchmarking gotcha: the provider caches identical requests** — re-running the same deck returns byte-identical output ~7x faster, so before/after timings must use a never-converted deck or the in-run overlap figure.
- `--no-caption-filter` — disables the caption filter cascade (`base.BaseConverter.filter_figures`, ON by default, shared by PDF/DOCX/PPTX). Two stages, cheap-and-certain first: **Stage A** `filter_figures_by_size` drops figures under `MIN_FIGURE_SIZE_PX` (64px) on either *native* axis; **Stage B1** `filter_figures_by_class` runs `figure_classifier.classify_figures` and drops a figure when its probability **summed over** `DECORATIVE_CATEGORIES` (`logo`, `icon`, `qr_code`, `bar_code`, `stamp`, `signature`) reaches `MIN_DECORATIVE_MASS` (0.80). Summing, not top-1: the classifier splits mass between `logo` and `icon` for the same picture (IBM DB2 wordmark: logo 0.35 / icon 0.34), so a top-1 threshold left obvious logos captioned. Threshold set from 152 eyeballed figures of a real deck, where content misread as decorative peaks at 0.658 and logos start at 0.575. The sum strictly contains the old rule (top-1 is part of it), verified: 51 drops became 86 with nothing un-dropped, all 35 additions confirmed decorative by eye. Dropped figures leave `figure_map` entirely, so they vanish from `document.md` and `images.md` (no heading, no link, no empty description); their PNG stays in `figures/` as an audit trail, and the run summary prints `N figure(s), M filtered out`. Everything fails open (unreadable image, missing model, low confidence, `other` label → keep and caption). Measured on real client decks: 25-56% fewer caption calls, 0 content loss across 100+ manually reviewed drops. Stage B2 (near-duplicate embedding clustering) is still unimplemented and is now low priority: classification already covers the repeated-logo case without needing a duplicate notion.

## Project Structure

```
src/
├── doc_convert/          # Main package
│   ├── __init__.py       # Re-exports app
│   ├── cli.py            # Typer CLI entry point + dispatch
│   ├── base.py           # BaseConverter + ConvertOptions
│   ├── image_prep.py     # Image size enforcement: ensure_image_under_limit (JPEG recompress before API send)
│   ├── figure_classifier.py # Caption filter Stage B1: docling's DocumentFigureClassifier (EfficientNet-B0), batched MPS/CPU, fail-open
│   ├── converters/       # One class per document type
│   │   ├── pdf.py        # PdfConverter (local Docling pipeline; captions via base.describe_figures; OCR engine via _build_ocr_options)
│   │   ├── docx.py       # DocxConverter (Docling native XML + figure captions)
│   │   ├── xlsx.py       # XlsxConverter
│   │   ├── pptx.py       # PptxConverter (Docling + python-pptx + figure captions)
│   │   ├── image.py      # ImageConverter (rasterize page → --llm; used for images and --engine llm)
│   │   ├── eml.py        # EmlConverter (mailparser + Docling HTML backend + recursive attachments)
│   │   ├── msg.py        # MsgConverter (extract-msg → EML bytes → EmlConverter)
│   │   └── media.py      # MediaConverter (audio/video → --llm + audio sections + video chunking)
│   ├── pptx_slide_vlm.py # PPTX whole-slide screenshot rendering (soffice+pypdfium2) + slide VLM interpretation via vision_llm + speaker notes
│   ├── vision_llm.py     # Shared vision primitive: one image + one prompt -> text, base64-aware size guard, retry/transient classification, map_concurrent fanout
│   ├── markdown.py       # build_document_markdown/build_pptx_slides_markdown (inlined figure/table descriptions), build_images_catalog, collect_floating_contexts
│   ├── vlm.py            # Caption description pipelines (local HF + external API)
│   ├── ocr_llm.py        # LlmOcrModel: cloud-LLM OCR engine for the local PDF pipeline (registers with docling's OCR factory)
│   ├── providers.py      # External LLM provider config + parsing (incl. get_ocr_prompt, RETRYABLE_HTTP_STATUS/RETRY_BACKOFF_SECONDS shared with media_llm)
│   ├── google_docs.py    # Google Docs/Sheets download
│   ├── formats.py        # Format detection, Engine enum, CaptionsSpec + OcrSpec parsing
│   ├── output.py         # Output dir helpers, cache check
│   ├── recursive.py      # convert_children helper (sub-doc recursion for email PJs + audio companions)
│   └── meeting_summary.py # --meeting-summary runtime (audio → summary.html via analyze model)
├── config.py             # Settings (pydantic-settings)
├── media_llm.py          # Direct LLM client for audio/video (Gemini Files API + OpenRouter)
├── audio.py              # Audio recording (sox) + transcription/analysis prompts
├── video.py              # Video processing + YouTube download (yt-dlp) + prompts
├── logging_config.py     # Rich logging setup
├── tracing.py            # OpenTelemetry tracing
└── py.typed              # PEP 561 marker
```

## Conventions

- CLI framework: Typer (not argparse)
- HTTP client: httpx (not requests)
- Config: pydantic-settings (not os.environ)
- Logging: Rich + logging module (not print)
- Conversion paths:
  - LLM OCR (`ocr_llm.py`) also uses `vision_llm`: regions are **cropped sequentially in the calling thread** because docling's PDF backends are not thread-safe, then the API calls fan out at `--llm-concurrency`, bounded in practice by docling's `page_batch_size` (4) times the regions per page. Crops travel as encoded bytes (`vision_llm.encode_pil`), so a dense page no longer churns one temp file per region. Measured on twin 20-page synthetic scans (two different PDFs, to defeat the provider cache): **756 s to 537 s (-29%)**, and content extraction went **19/20 to 20/20** because the old path dropped a region on any exception with no retry: a single `ReadTimeoutError` silently lost a whole page, and docling's own retry has `read=0` so read timeouts were never retried. Overlap is only ~1.5x, not 4x, because a batch's wall clock is its slowest call and OCR latency varies widely.
  - PDF `--engine local` (default): Docling layout + OCR + tables; figures captioned via `BaseConverter.describe_figures` (honors `--captions`). OCR engine selected by `--ocr-model` (default cloud LLM `ibm/gemini-3.7-flash` via `ocr_llm.LlmOcrModel`; or `local` Tesseract CLI). Docling's flaky "auto" OCR is bypassed: we always set an explicit `ocr_options`.
  - PDF `--engine llm`: rasterize each page → `--llm` (whole page markdown). Same code path as Image.
  - Image: always `--engine llm`. If `--llm` is omitted on a direct image input, the CLI auto-fills `ibm/gemini-3.7-flash`. If the resolved model returns empty markdown, `ImageConverter.convert` fails loudly with `typer.Exit(1)` instead of writing a 0-byte `document.md`. Re-run with `-vv` to see the API response.
  - DOCX: native XML for body, `describe_figures` for picture captions.
  - PPTX: native XML (docling) for body text + `describe_figures` for picture captions, **plus by default** a whole-slide screenshot pass (`pptx_slide_vlm.py`): each slide is rendered faithfully to PNG via headless LibreOffice (`soffice --headless --convert-to pdf`, then `pypdfium2` rasterizes the resulting PDF page — reusing real PowerPoint rendering instead of reimplementing layout) and sent alone to a vision LLM (default `ibm/gemini-3.7-flash`, override with `--slide-vlm`; 8 slides at a time, see `--llm-concurrency`) for a detailed visual interpretation (layout, charts/diagrams explained, not a literal transcription). `document.md` groups PPTX output **by slide**, each with 3 labelled subsections: `### Extracted Content (text + figures)` (the mechanical docling+python-pptx parse), `### Visual Interpretation (full slide screenshot)` (the VLM's read of the rendered slide), `### Speaker Notes` (python-pptx `notes_slide.notes_text_frame.text`, or a placeholder). This lets a downstream LLM tell "what was mechanically extracted" apart from "what a vision model saw". Rendered screenshots land in `<output>/slides/slide_NNN.png`. Disable with `--no-slide-screenshots` (falls back to the old flat, non-grouped `document.md`). Requires LibreOffice (`brew install libreoffice`) for rendering and an IBM ICA credential (or `--slide-vlm`/`--llm` override) for the analysis call.
  - XLSX: native, no LLM.
  - EML / MSG: email HTML body rendered via `html2text` (after a BeautifulSoup clean pass) — `eml._html_to_markdown`. This replaces Docling's HTMLDocumentBackend, which collapsed the nested layout tables of marketing/HR emails into a single markdown table cell and dropped every link URL. The clean pass (`eml._clean_email_html`) strips tracking pixels / spacer gifs / empty tracking anchors and **unwraps layout tables** (`eml._flatten_layout_tables` + `_is_layout_table` heuristic: role=presentation, nested table, block-level cell content, or <2 rows/cols → layout) while leaving **genuine data tables** (header cells, inline-only, ≥2×2) intact so `pad_tables=True` renders them as aligned markdown tables. Post-processing turns html2text's escaped literal dashes (`\- text`) into tight markdown bullet lists and drops empty `**` and zero-width filler. Remote images are never fetched (privacy: avoids open-tracking beacons). Attachments are recursively converted under `attachments/<basename>_docling/`, inlined as `## Attachments` sub-sections.
  - Audio: default `ibm/gemini-3.7-flash`. `src/audio_prep.py` first normalises the source (mp3/opus/m4a/wav/... incl. `.opus`) to a mono 16 kHz OGG Opus @ 32 kbps working copy at `<output>/audio.ogg` (~14 MB/h; skipped only if the source is already mono ≤16 kHz ogg). For inline providers (ibm/openrouter) a recording is split into equal parts with a 1-minute overlap under `<output>/parts/part_NN.ogg` when it exceeds **either** `SIZE_LIMIT_MB` (50 MB, ~3.5 h of base64 payload) **or** `DURATION_LIMIT_SECONDS` (20 min per request). The duration cap exists because IBM ICA sits behind a gateway that can't handle a long transcription: it either returns a **524 after ~10 min** or just **hangs until the client read timeout fires** (httpx `ReadTimeout`). Measured: 25 min parts transcribe reliably, 29 min parts intermittently hang, so the ceiling is a safe 20 min. A 2 h recording is only ~20 MB (under the size limit) but must still split by time. `plan_parts` uses `max(size_parts, duration_parts)`. Each part is transcribed independently (the previous part's tail + speaker roster is injected into the next part's system prompt for continuity), then stitched under `## Part N of M (HH:MM:SS to HH:MM:SS)` headers with a note about the overlap. `google/` never splits (Files API has no size or gateway-timeout limit). Every inline request is retried up to `_MAX_MEDIA_ATTEMPTS` (4) with backoff on **both** transient HTTP statuses (429/500/502/503/504/520/522/524) **and** httpx transport/timeout **exceptions** (`ReadTimeout`, `ConnectError`, ...) via `media_llm._send_media_request`; a 502 is only treated as "payload too large" when the raw payload actually exceeds `_INLINE_TOO_LARGE_MB` (60 MB), otherwise it is a transient gateway flake. On final exhaustion it raises a clear message pointing at the `google/` provider. Then `## Additional Context`, `## Screenshots`, `## Additional Documents` are appended when a companion .md is present. Companion context is also injected into the transcription system prompt (kept for spelling consistency). `--analyze` resends the normalised single-part ogg; if the audio was split it falls back to analysing the `document.md` transcript text.
  - Video: same multimodal model as audio. Videos longer than 30 min are split into 30 min chunks via ffmpeg stream-copy; each chunk is processed independently, then a text-only meta-summary is prepended as `## Executive Summary`.
  - `--analyze` pass: text-only on the consolidated `document.md`, writes `analyze.md`. Default model `ibm/gemini-3.7-flash`; override via `--analyze-model`. Custom prompt via `--analyze-prompt`. `--instructions` is a hidden deprecated alias.
  - `--meeting-summary` (audio only): same analysis LLM, produces `summary.html`, opened via `open` after generation.
- Figure and table descriptions are unified across PDF/DOCX/PPTX in `base.describe_figures` / `base.describe_tables`, dispatching on `CaptionsSpec` (CaptionsOff / CaptionsLocal / CaptionsLlm). Tables are rendered as PNG via `TableItem.get_image(doc)` into `<output>/tables/` and described by the same captioner.
- Caption filter (`base.filter_figures`, see `--no-caption-filter` above) sits between figure extraction and `describe_figures` in all three converters, so a dropped figure costs no caption call **and** never reaches the markdown. It runs even with `--captions off` (a deck's 50 logo entries are noise in `document.md` whatever the captioner, and `--analyze` reads that file). Tables are never filtered. The classifier model is lazy-loaded on first use; `--download-models` pre-warms it via `figure_classifier.prefetch()` for offline machines but is not required.
- Repeated figures are described once (`markdown._render_figure_lines` + `seen_figures`): the API call was already deduplicated by content hash, but the description text was printed once per placement. A banner on 36 slides was 24% of a 688 KB `document.md`. Later placements keep heading, image link and their own per-occurrence citing sentence, and get `> *Same image as under Slide N; description given there.*`. `build_images_catalog` groups by image file (`_collect_catalog_entries`), one entry per distinct file with every page/slide it appears on, merging captions/mentions from all placements: 212 entries for 99 files became 99. Measured: `document.md` 688->531 KB (-23%), `images.md` 314->146 KB (-54%), with 99/99 descriptions, 212 links, 212 headings, 213 mentions intact and extracted text + speaker notes byte-identical on 98/98 slides. Trade-off to know: a per-slide chunk of `document.md` holding a repeated figure has a pointer, not the description.
- `document.md` is self-contained: each figure/table gets a `#### Figure N: caption` heading, its VLM description in a blockquote, the body sentence that cites it (when detected via `Figure N` / `Table N` regex in `markdown.collect_floating_contexts`), and (figures only) a link to `figures/figure_N.png`. Images are NOT embedded inline. `images.md` is the complete sidecar catalog with thumbnails.
- PDF pipeline defaults: `TableStructureV2Options`, `do_formula_enrichment=True`, `images_scale=2.0` (sharper crops for the captioner). `do_chart_extraction` is intentionally OFF: the cloud captioner already extracts chart axes/values as a markdown table via the figure caption prompt, and Granite Vision (~5 GB) would be a redundant download. First run still downloads the formula model from Hugging Face into `~/.cache/huggingface/`; subsequent runs reuse the cache.
- Cloud captioners receive a per-image context block (caption + body mention) prepended to the prompt (`providers.build_context_block`). The local SmolVLM captioner ignores per-image context because the prompt is set on the model instance.
- Offline by default for local captioner: HF model pre-downloaded to `~/.cache/models` via `doc-convert --download-models [--captions <preset>]`.
- External LLM providers: `google/<model>` (GenAI), `openrouter/<model>`, `ibm/<model>` (OpenAI-compatible; `DOC_CONVERT_IBM_ICA_MODEL_KEY` + `DOC_CONVERT_IBM_ICA_BASE_URL`). IBM uses base64 inline for audio/video (no Files API).
- Cache: skip conversion if output exists, use `-f` to force.
- Unified output: all conversions write to `<name>_docling/document.md`.
- Apple Silicon: PDF pipeline and local captioner auto-retry on CPU when an MPS float64 error is raised (detection in `vlm.is_mps_float64_error`). `--cpu` is still available to force CPU from the start.
- Base64 budget (`vision_llm.encode_image`): provider limits apply to the **base64 payload**, which is 4/3 the bytes on disk, so the file-size budget is scaled by 3/4 before `ensure_image_under_limit` runs. Without it a 4.3 MB PNG passed the guard untouched, the API answered 400, and that figure silently lost its caption (reproduced and fixed; regression test asserts the encoded payload, not the file). The same off-by-4/3 still exists in the docling-side guard used by `ocr_llm` and `converters/image`; fix it when those move off docling.
- Image size guard (`doc_convert.image_prep`): every image sent to a cloud LLM (captions, figure descriptions, OCR crops, PPTX slide screenshots, companion images) passes through `ensure_image_under_limit` before encoding. Images >5 MB are converted to JPEG at quality 90 and quality is reduced by 10% per step down to 20%; if still oversized the image is halved in resolution and the loop restarts (up to 4 halvings). A warning is logged whenever any compression or downscale occurs. The original file is never modified; a tmp `.jpg` is created and auto-deleted via the `PreparedImage` context manager. PDFs skip this path entirely.
- Output guard (`doc_convert.output_guard`): every output dir is registered before mkdir; on any failure (exception, Ctrl+C, SIGTERM/SIGHUP/SIGQUIT, typer.Exit), `cleanup_pending()` runs in a `finally` block and removes the dir if it was newly created and contains no **non-empty** `document.md` or `audio.ogg` (a 0-byte marker is a silently-failed conversion, not an artifact, and is cleaned up too). Pre-existing dirs keep their original content; only files added by the failed run are removed. SIGKILL cannot be caught.

## Quality Gate

Run `make check` before every commit. It runs: lint, format-check, typecheck, security, test-cov.

## Coding Standards

This project follows the `python` skill. Reload it for full coding standards reference.
