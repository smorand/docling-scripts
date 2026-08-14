"""Base converter class and shared options."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from config import Settings
from doc_convert.formats import (
    DEFAULT_LOCAL_PRESET,
    DEFAULT_OCR_ENGINE,
    CaptionsLlm,
    CaptionsLocal,
    CaptionsOff,
    CaptionsSpec,
    Engine,
    OcrLocal,
    OcrOff,
    OcrSpec,
)
from doc_convert.output import print_output_summary
from doc_convert.providers import DEFAULT_LLM_CONCURRENCY

logger = logging.getLogger(__name__)

# Stage A of the caption filter cascade: a figure whose native resolution is
# below this floor on either axis cannot physically carry legible content
# (chart, photo, diagram), so it is dropped before ever reaching a captioner.
# Conservative on purpose: real content in the wild is essentially never this
# small, so this floor should never cut a detail an LLM caption could have
# described. Disable the whole cascade with --no-caption-filter.
MIN_FIGURE_SIZE_PX = 64

_FORMAT_NAMES: dict[str, str] = {
    ".pdf": "PDF",
    ".docx": "Word (DOCX)",
    ".xlsx": "Excel (XLSX)",
    ".pptx": "PowerPoint (PPTX)",
    ".pptm": "PowerPoint (PPTM)",
    ".potx": "PowerPoint (POTX)",
    ".ppsx": "PowerPoint (PPSX)",
    ".eml": "Email (EML)",
    ".ogg": "Audio (OGG)",
    ".mp3": "Audio (MP3)",
    ".wav": "Audio (WAV)",
    ".m4a": "Audio (M4A)",
    ".mp4": "Video (MP4)",
    ".mkv": "Video (MKV)",
    ".mov": "Video (MOV)",
    ".jpg": "Image (JPEG)",
    ".jpeg": "Image (JPEG)",
    ".png": "Image (PNG)",
}


def _ext_to_format_name(ext: str) -> str:
    return _FORMAT_NAMES.get(ext.lower(), ext.upper().lstrip("."))


def _looks_like_short_title(text: str, *, max_len: int = 200) -> bool:
    """True when ``text`` is short enough to safely use as a top-level title.

    Long ``meeting`` strings (e.g. a merged companion bundle) must not be
    written verbatim as the analyze.md H1, otherwise the bundle leaks into
    the analysis output.
    """
    if not text:
        return False
    if len(text) > max_len:
        return False
    return "\n\n" not in text


@dataclass(frozen=True)
class ConvertOptions:
    """Options passed from CLI to every converter."""

    output_dir: Path
    figures: bool = True
    all_formats: bool = False
    do_ocr: bool = True
    cpu: bool = False
    engine: Engine = Engine.LOCAL
    captions: CaptionsSpec = field(default_factory=lambda: CaptionsLocal(DEFAULT_LOCAL_PRESET))
    ocr: OcrSpec = field(default_factory=lambda: OcrLocal(DEFAULT_OCR_ENGINE))
    llm: str | None = None
    slide_screenshots: bool = True
    slide_vlm: str | None = None
    llm_concurrency: int = DEFAULT_LLM_CONCURRENCY
    caption_filter: bool = True
    settings: Settings = field(default_factory=Settings)

    @property
    def models_path(self) -> Path:
        return Path(self.settings.models_path)

    @property
    def captions_enabled(self) -> bool:
        return not isinstance(self.captions, CaptionsOff)

    @property
    def ocr_enabled(self) -> bool:
        """OCR runs only when not disabled by --no-ocr and the spec is not 'off'."""
        return self.do_ocr and not isinstance(self.ocr, OcrOff)


class BaseConverter(ABC):
    """Base class for all document converters."""

    def __init__(self, source: Path, options: ConvertOptions) -> None:
        self.source = source
        self.options = options
        self.output_dir = options.output_dir

    @abstractmethod
    def convert(self) -> None:
        """Run the conversion, writing results to self.output_dir."""

    def ensure_output_dir(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_document_md(self, content: str) -> None:
        (self.output_dir / "document.md").write_text(content)

    def write_all_formats(self, doc: object) -> None:
        """Export Docling document to all output formats."""
        (self.output_dir / "output.md").write_text(doc.export_to_markdown())  # type: ignore[attr-defined]
        (self.output_dir / "output.json").write_text(
            json.dumps(doc.export_to_dict(), indent=2, default=str)  # type: ignore[attr-defined]
        )
        (self.output_dir / "output.txt").write_text(doc.export_to_text())  # type: ignore[attr-defined]
        (self.output_dir / "output.html").write_text(doc.export_to_html())  # type: ignore[attr-defined]

    def extract_figures_from_doc(self, doc: object) -> tuple[dict[str, str], list[Path], list[str]]:
        """Extract PictureItem images from a Docling document.

        Deduplicates images by content hash: identical images (logos, icons
        repeated across pages) are saved only once.

        Returns (figure_map, image_paths, item_refs).
        """
        import hashlib  # noqa: PLC0415
        import io  # noqa: PLC0415

        from docling_core.types.doc import PictureItem  # noqa: PLC0415

        figure_map: dict[str, str] = {}
        image_paths: list[Path] = []
        item_refs: list[str] = []
        fig_count = 0
        seen_hashes: dict[str, Path] = {}
        dedup_count = 0

        fig_dir = self.output_dir / "figures"
        fig_dir.mkdir(exist_ok=True)
        for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
            if isinstance(item, PictureItem):
                try:
                    img = item.get_image(doc)  # type: ignore[arg-type]
                except Exception:
                    logger.warning("Failed to extract image: %s", item.self_ref)
                    continue
                if img:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    img_hash = hashlib.md5(buf.getvalue(), usedforsecurity=False).hexdigest()

                    if img_hash in seen_hashes:
                        existing = seen_hashes[img_hash]
                        figure_map[item.self_ref] = f"figures/{existing.name}"
                        image_paths.append(existing)
                        item_refs.append(item.self_ref)
                        dedup_count += 1
                    else:
                        filename = f"figure_{fig_count}.png"
                        filepath = fig_dir / filename
                        filepath.write_bytes(buf.getvalue())
                        seen_hashes[img_hash] = filepath
                        figure_map[item.self_ref] = f"figures/{filename}"
                        image_paths.append(filepath)
                        item_refs.append(item.self_ref)
                        fig_count += 1

        total = fig_count + dedup_count
        if dedup_count > 0:
            logger.info(
                "Extracted %d unique figure(s) from %d total (%d duplicates removed)", fig_count, total, dedup_count
            )
        else:
            logger.info("Extracted %d figure(s)", fig_count)
        return figure_map, image_paths, item_refs

    def filter_figures_by_size(
        self,
        figure_map: dict[str, str],
        image_paths: list[Path],
        item_refs: list[str],
        *,
        min_size: int = MIN_FIGURE_SIZE_PX,
    ) -> tuple[dict[str, str], list[Path], list[str]]:
        """Drop figures whose native resolution is below ``min_size`` on either axis.

        Stage A of the caption filter cascade (see ``MIN_FIGURE_SIZE_PX``). A
        dropped figure is removed from ``figure_map`` too, so it never appears
        in document.md/images.md and is never sent to a captioner. No-op when
        ``ConvertOptions.caption_filter`` is False (--no-caption-filter).

        Unreadable images are kept (fail-open): we only drop what we can
        positively confirm is too small, never on an error.
        """
        if not self.options.caption_filter:
            return figure_map, image_paths, item_refs

        from PIL import Image  # noqa: PLC0415

        kept_map: dict[str, str] = {}
        kept_paths: list[Path] = []
        kept_refs: list[str] = []
        size_cache: dict[Path, bool] = {}
        dropped = 0

        for path, ref in zip(image_paths, item_refs, strict=True):
            large_enough = size_cache.get(path)
            if large_enough is None:
                try:
                    with Image.open(path) as img:
                        width, height = img.size
                    large_enough = width >= min_size and height >= min_size
                except Exception:
                    logger.warning("Caption filter: failed to read size of %s, keeping it", path)
                    large_enough = True
                size_cache[path] = large_enough

            if large_enough:
                kept_paths.append(path)
                kept_refs.append(ref)
                kept_map[ref] = figure_map[ref]
            else:
                dropped += 1

        if dropped:
            logger.info(
                "Caption filter: dropped %d figure(s) under %dpx (size floor); %d remain",
                dropped,
                min_size,
                len(kept_paths),
            )
        return kept_map, kept_paths, kept_refs

    def filter_figures_by_class(
        self,
        figure_map: dict[str, str],
        image_paths: list[Path],
        item_refs: list[str],
    ) -> tuple[dict[str, str], list[Path], list[str]]:
        """Drop figures the document figure classifier calls purely decorative.

        Stage B1 of the caption filter cascade: docling's own EfficientNet-B0
        figure classifier labels each image (logo, icon, chart, photograph,
        ...) and anything whose summed probability over ``DECORATIVE_CATEGORIES``
        reaches
        ``MIN_DECORATIVE_MASS`` is dropped before it reaches a paid captioner.

        Each distinct file is classified once, even when several items point at
        it (post exact-hash dedup). Fail-open: an unavailable model or a failed
        batch yields ``UNKNOWN``, which is never decorative, so nothing is
        dropped. No-op when ``--no-caption-filter`` is set.
        """
        if not self.options.caption_filter or not image_paths:
            return figure_map, image_paths, item_refs

        from doc_convert.figure_classifier import classify_figures  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        unique_paths = list(dict.fromkeys(image_paths))
        with trace_span("caption_filter.classify", count=len(unique_paths)):
            verdicts = classify_figures(unique_paths)
        verdict_by_path = dict(zip(unique_paths, verdicts, strict=True))

        kept_map: dict[str, str] = {}
        kept_paths: list[Path] = []
        kept_refs: list[str] = []
        dropped_by_label: dict[str, int] = {}

        for path, ref in zip(image_paths, item_refs, strict=True):
            verdict = verdict_by_path[path]
            if verdict.is_decorative:
                dropped_by_label[verdict.label] = dropped_by_label.get(verdict.label, 0) + 1
                continue
            kept_paths.append(path)
            kept_refs.append(ref)
            kept_map[ref] = figure_map[ref]

        if dropped_by_label:
            breakdown = ", ".join(f"{label} x{count}" for label, count in sorted(dropped_by_label.items()))
            logger.info(
                "Caption filter: dropped %d decorative figure(s) (%s); %d remain",
                sum(dropped_by_label.values()),
                breakdown,
                len(kept_paths),
            )
        return kept_map, kept_paths, kept_refs

    def filter_figures(
        self,
        figure_map: dict[str, str],
        image_paths: list[Path],
        item_refs: list[str],
    ) -> tuple[dict[str, str], list[Path], list[str]]:
        """Run the full caption filter cascade before any captioning happens.

        Cheap and certain first, model-based judgement second:
          Stage A: native size floor (``MIN_FIGURE_SIZE_PX``), free, no model.
          Stage B1: document figure classifier, drops confident decorative art.

        Dropped figures leave ``figure_map`` entirely, so they never show up in
        document.md or images.md. Disabled as a whole by ``--no-caption-filter``.
        """
        if not self.options.caption_filter:
            return figure_map, image_paths, item_refs
        figure_map, image_paths, item_refs = self.filter_figures_by_size(figure_map, image_paths, item_refs)
        return self.filter_figures_by_class(figure_map, image_paths, item_refs)

    def extract_table_images(self, doc: object) -> tuple[list[Path], list[str]]:
        """Render every TableItem as a PNG so the VLM can describe it.

        Tables are saved as temporary PNGs in ``<output_dir>/tables/`` (not
        kept in the final ``document.md`` — only the markdown serialisation is).
        Returns (image_paths, item_refs) in document reading order.
        """
        import io  # noqa: PLC0415

        from docling_core.types.doc import TableItem  # noqa: PLC0415

        image_paths: list[Path] = []
        item_refs: list[str] = []
        tbl_count = 0

        tbl_dir = self.output_dir / "tables"
        tbl_dir.mkdir(exist_ok=True)
        for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
            if not isinstance(item, TableItem):
                continue
            try:
                img = item.get_image(doc)  # type: ignore[arg-type]
            except Exception:
                logger.warning("Failed to render table: %s", item.self_ref)
                continue
            if not img:
                continue
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            filename = f"table_{tbl_count}.png"
            filepath = tbl_dir / filename
            filepath.write_bytes(buf.getvalue())
            image_paths.append(filepath)
            item_refs.append(item.self_ref)
            tbl_count += 1

        if tbl_count > 0:
            logger.info("Rendered %d table image(s) for VLM description", tbl_count)
        return image_paths, item_refs

    def _describe_artifacts(
        self,
        image_paths: list[Path],
        item_refs: list[str],
        span_name: str,
        prompt: str,
        contexts_by_ref: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Shared captioning entry point for figures and tables.

        ``contexts_by_ref`` maps a ref to a context block (caption + mention)
        that the external LLM path will prepend to the prompt. The local VLM
        path ignores it (no cheap per-image prompts).

        Deduplicates: if the same image file is referenced by multiple items,
        it is described only once and the description is reused.
        """
        from doc_convert.vlm import describe_images_with_external_llm, describe_images_with_vlm  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        descriptions: dict[str, str] = {}
        if not self.options.captions_enabled or not image_paths:
            return descriptions

        # Dedup identical files; first ref wins for context selection.
        unique_paths: list[Path] = []
        unique_contexts: list[str] = []
        seen: dict[str, int] = {}
        for path, ref in zip(image_paths, item_refs, strict=True):
            key = str(path)
            if key in seen:
                continue
            seen[key] = len(unique_paths)
            unique_paths.append(path)
            unique_contexts.append((contexts_by_ref or {}).get(ref, ""))

        if len(unique_paths) < len(image_paths):
            logger.info("Caption dedup: %d unique images from %d total", len(unique_paths), len(image_paths))

        captions = self.options.captions
        with trace_span(span_name, count=len(unique_paths)):
            if isinstance(captions, CaptionsLlm):
                logger.info("Describing with %s/%s", captions.provider, captions.model)
                desc_list = describe_images_with_external_llm(
                    unique_paths,
                    captions.provider,
                    captions.model,
                    self.options.settings,
                    prompt=prompt,
                    contexts=unique_contexts,
                )
            elif isinstance(captions, CaptionsLocal):
                desc_list = describe_images_with_vlm(
                    unique_paths, captions.preset, self.options.models_path, prompt=prompt
                )
            else:
                return descriptions

        path_to_desc: dict[str, str] = {}
        for path, desc in zip(unique_paths, desc_list, strict=True):
            if desc:
                path_to_desc[str(path)] = desc

        for ref, path in zip(item_refs, image_paths, strict=True):
            desc = path_to_desc.get(str(path), "")
            if desc:
                descriptions[ref] = desc

        return descriptions

    def describe_figures(
        self,
        image_paths: list[Path],
        item_refs: list[str],
        span_name: str,
        contexts_by_ref: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Describe figures (PictureItems) according to ``ConvertOptions.captions``."""
        from doc_convert.providers import get_caption_prompt  # noqa: PLC0415

        return self._describe_artifacts(image_paths, item_refs, span_name, get_caption_prompt(), contexts_by_ref)

    def describe_tables(
        self,
        image_paths: list[Path],
        item_refs: list[str],
        span_name: str,
        contexts_by_ref: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Describe tables according to ``ConvertOptions.captions``."""
        from doc_convert.providers import get_table_prompt  # noqa: PLC0415

        return self._describe_artifacts(image_paths, item_refs, span_name, get_table_prompt(), contexts_by_ref)

    def run_analysis(
        self,
        llm: str | None,
        instructions: str | None = None,
        meeting: str | None = None,
        lang: str | None = None,
        depth: int = 3,
    ) -> bool:
        """Run LLM analysis on document.md, write analyze.md.

        Uses the adaptive analysis prompt from analysis_prompt.py unless
        overridden by custom instructions via --analyze-prompt.

        Args:
            llm: Provider/model override (default: ibm/claude-opus-4-8)
            instructions: Custom prompt (overrides the default analysis prompt entirely)
            meeting: Context to inject into the prompt
            lang: Output language (e.g. "fr", "en"). None = auto-detect from document.

        Returns True if analysis was written.
        """
        import httpx  # noqa: PLC0415

        from doc_convert.providers import get_provider_url, resolve_document_analysis_llm  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        doc_md_path = self.output_dir / "document.md"
        if not doc_md_path.exists():
            return False

        doc_content = doc_md_path.read_text()
        provider, model, api_key = resolve_document_analysis_llm(llm, self.options.settings)

        source_name = self.source.name
        source_format = _ext_to_format_name(self.source.suffix)

        if instructions:
            system = instructions
            prompt = f"Analyze this document.\n\nSource file: {source_name} (format: {source_format})\n\n{doc_content}"
        else:
            from doc_convert.analysis_prompt import (  # noqa: PLC0415
                get_document_analysis_system_prompt,
                get_document_analysis_user_prompt,
            )

            system = get_document_analysis_system_prompt(depth=depth)
            prompt = get_document_analysis_user_prompt().format(
                content=doc_content, source_name=source_name, source_format=source_format
            )

        if meeting:
            system = f"Context: {meeting}\n\n{system}"
        if lang:
            lang_rule = (
                f"HARD RULE: Write the ENTIRE response in {lang}, including all section headings, "
                f"table headers, and content. Do NOT switch language even if the source material or "
                f"injected context is in another language."
            )
            system = f"{lang_rule}\n\n{system}\n\n{lang_rule}"

        with trace_span("document.analyze", file=self.source.name, provider=provider):
            logger.info("Analyzing %s with %s/%s", self.source.name, provider, model)
            with httpx.Client(timeout=300.0) as client:
                resp = client.post(
                    get_provider_url(provider, self.options.settings),
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                resp.raise_for_status()

        analysis_md = resp.json()["choices"][0]["message"]["content"]
        if meeting and _looks_like_short_title(meeting):
            analysis_md = f"# {meeting}\n\n{analysis_md}"
        (self.output_dir / "analyze.md").write_text(analysis_md)
        logger.info("Analysis written to %s/analyze.md", self.output_dir)
        return True

    def print_summary(
        self,
        fig_count: int = 0,
        captions_used: bool = False,
        desc_count: int = 0,
        extra_files: list[str] | None = None,
        filtered_count: int = 0,
    ) -> None:
        print_output_summary(
            self.output_dir,
            fig_count=fig_count,
            all_formats=self.options.all_formats,
            vlm_used=captions_used,
            desc_count=desc_count,
            extra_files=extra_files,
            filtered_count=filtered_count,
        )
