"""Base converter class and shared options."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from config import Settings
from doc_convert.output import print_output_summary

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class ConvertOptions:
    """Options passed from CLI to every converter."""

    output_dir: Path
    vlm: bool = True
    vlm_preset: str = "smolvlm"
    figures: bool = True
    all_formats: bool = False
    do_ocr: bool = True
    cpu: bool = False
    external_llm: tuple[str, str] | None = None
    settings: Settings = field(default_factory=Settings)

    @property
    def models_path(self) -> Path:
        return Path(self.settings.models_path)


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

        from docling_core.types.doc.document import PictureItem  # noqa: PLC0415

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
                    img = item.get_image(doc)
                except Exception:
                    logger.warning("Failed to extract image: %s", item.self_ref)
                    continue
                if img:
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    img_hash = hashlib.md5(buf.getvalue()).hexdigest()

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

    def describe_figures(self, image_paths: list[Path], item_refs: list[str], span_name: str) -> dict[str, str]:
        """Run VLM descriptions on extracted figures. Returns {ref: description}.

        Deduplicates: if the same image file is referenced by multiple items,
        it is described only once and the description is reused.
        """
        from doc_convert.vlm import describe_images_with_external_llm, describe_images_with_vlm  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        figure_descriptions: dict[str, str] = {}
        if not (self.options.vlm and image_paths):
            return figure_descriptions

        # Deduplicate: describe each unique file only once
        unique_paths: list[Path] = []
        seen: set[str] = set()
        for p in image_paths:
            key = str(p)
            if key not in seen:
                unique_paths.append(p)
                seen.add(key)

        if len(unique_paths) < len(image_paths):
            logger.info("VLM dedup: %d unique images from %d total", len(unique_paths), len(image_paths))

        # Auto-detect: use Google Gemini 3.1 Flash Lite if API key is available
        use_external = self.options.external_llm
        if not use_external and self.options.settings.google_api_key:
            use_external = ("google", "gemini-3.1-flash-lite-preview")
            logger.info("Auto-using google/gemini-3.1-flash-lite-preview for image descriptions")

        with trace_span(span_name, count=len(unique_paths)):
            if use_external:
                provider, model = use_external
                desc_list = describe_images_with_external_llm(unique_paths, provider, model, self.options.settings)
            else:
                desc_list = describe_images_with_vlm(unique_paths, self.options.vlm_preset, self.options.models_path)

        # Build path -> description lookup
        path_to_desc: dict[str, str] = {}
        for p, desc in zip(unique_paths, desc_list, strict=True):
            if desc:
                path_to_desc[str(p)] = desc

        # Map back to item refs (including duplicates)
        for ref, p in zip(item_refs, image_paths, strict=True):
            desc = path_to_desc.get(str(p), "")
            if desc:
                figure_descriptions[ref] = desc

        return figure_descriptions

    def run_analysis(
        self,
        use_external_llm: str | None,
        instructions: str | None = None,
        meeting: str | None = None,
        lang: str | None = None,
        depth: int = 3,
    ) -> bool:
        """Run LLM analysis on document.md, write analysis.md.

        Uses the adaptive analysis prompt from analysis_prompt.py unless
        overridden by custom instructions via -i/--instructions.

        Args:
            use_external_llm: Provider/model override (default: openrouter/google/gemini-2.5-flash)
            instructions: Custom prompt (overrides the default analysis prompt entirely)
            meeting: Context to inject into the prompt
            lang: Output language (e.g. "fr", "en"). None = auto-detect from document.

        Returns True if analysis was written.
        """
        import httpx  # noqa: PLC0415

        from doc_convert.providers import get_provider_url, resolve_media_llm  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        doc_md_path = self.output_dir / "document.md"
        if not doc_md_path.exists():
            return False

        doc_content = doc_md_path.read_text()
        provider, model, api_key = resolve_media_llm(use_external_llm, self.options.settings)

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
        if meeting:
            analysis_md = f"# {meeting}\n\n{analysis_md}"
        (self.output_dir / "analysis.md").write_text(analysis_md)
        logger.info("Analysis written to %s/analysis.md", self.output_dir)
        return True

    def print_summary(
        self,
        fig_count: int = 0,
        vlm_used: bool = False,
        desc_count: int = 0,
        extra_files: list[str] | None = None,
    ) -> None:
        print_output_summary(
            self.output_dir,
            fig_count=fig_count,
            all_formats=self.options.all_formats,
            vlm_used=vlm_used,
            desc_count=desc_count,
            extra_files=extra_files,
        )
