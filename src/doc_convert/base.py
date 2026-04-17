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


@dataclass(frozen=True)
class ConvertOptions:
    """Options passed from CLI to every converter."""

    output_dir: Path
    vlm: bool = True
    vlm_preset: str = "smolvlm"
    figures: bool = True
    all_formats: bool = False
    do_ocr: bool = True
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

        Returns (figure_map, image_paths, item_refs).
        """
        from docling_core.types.doc.document import PictureItem  # noqa: PLC0415

        figure_map: dict[str, str] = {}
        image_paths: list[Path] = []
        item_refs: list[str] = []
        fig_count = 0

        fig_dir = self.output_dir / "figures"
        fig_dir.mkdir(exist_ok=True)
        for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
            if isinstance(item, PictureItem):
                img = item.get_image(doc)
                if img:
                    filename = f"figure_{fig_count}.png"
                    img.save(fig_dir / filename)
                    figure_map[item.self_ref] = f"figures/{filename}"
                    image_paths.append(fig_dir / filename)
                    item_refs.append(item.self_ref)
                    fig_count += 1

        return figure_map, image_paths, item_refs

    def describe_figures(self, image_paths: list[Path], item_refs: list[str], span_name: str) -> dict[str, str]:
        """Run VLM descriptions on extracted figures. Returns {ref: description}."""
        from doc_convert.vlm import describe_images_with_external_llm, describe_images_with_vlm  # noqa: PLC0415
        from tracing import trace_span  # noqa: PLC0415

        figure_descriptions: dict[str, str] = {}
        if not (self.options.vlm and image_paths):
            return figure_descriptions

        with trace_span(span_name, count=len(image_paths)):
            if self.options.external_llm:
                provider, model = self.options.external_llm
                desc_list = describe_images_with_external_llm(image_paths, provider, model, self.options.settings)
            else:
                desc_list = describe_images_with_vlm(image_paths, self.options.vlm_preset, self.options.models_path)
            for ref, desc in zip(item_refs, desc_list, strict=True):
                if desc:
                    figure_descriptions[ref] = desc

        return figure_descriptions

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
