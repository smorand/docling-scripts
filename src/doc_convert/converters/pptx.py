"""PPTX converter: Docling native + python-pptx images + VLM descriptions."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003

from doc_convert.base import BaseConverter
from doc_convert.markdown import build_images_catalog, build_page_annotated_markdown, extract_title

logger = logging.getLogger(__name__)


def _extract_pptx_images(pptx_path: Path, output_dir: Path) -> dict[int, list[Path]]:
    """Extract images from PPTX using python-pptx with group recursion."""
    from pptx import Presentation as PptxPresentation  # noqa: PLC0415
    from pptx.enum.shapes import MSO_SHAPE_TYPE  # noqa: PLC0415

    prs = PptxPresentation(str(pptx_path))
    fig_dir = output_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    images: dict[int, list[Path]] = {}
    fig_count = 0

    def process_shape(shape: object, slide_num: int) -> None:
        nonlocal fig_count
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:  # type: ignore[attr-defined]
            for grouped in shape.shapes:  # type: ignore[attr-defined]
                process_shape(grouped, slide_num)
        elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:  # type: ignore[attr-defined]
            try:
                image = shape.image  # type: ignore[attr-defined]
                content_type = image.content_type
                ext = content_type.split("/")[-1]
                if ext == "jpeg":
                    ext = "jpg"
                elif ext not in ("png", "jpg", "gif", "bmp", "tiff", "webp"):
                    ext = "png"
                filename = f"slide{slide_num}_fig{fig_count}.{ext}"
                filepath = fig_dir / filename
                filepath.write_bytes(image.blob)
                images.setdefault(slide_num, []).append(filepath)
                fig_count += 1
            except Exception:
                logger.warning("Failed to extract image from slide %d", slide_num)

    for slide_idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            process_shape(shape, slide_idx + 1)

    logger.info("Extracted %d image(s) from %d slide(s)", fig_count, len(prs.slides))
    return images


def _map_images_to_items(
    doc: object, slide_images: dict[int, list[Path]]
) -> tuple[dict[str, str], list[Path], list[str]]:
    """Map python-pptx extracted images to Docling PictureItems by slide + order."""
    from docling_core.types.doc.document import PictureItem  # noqa: PLC0415

    figure_map: dict[str, str] = {}
    image_paths: list[Path] = []
    item_refs: list[str] = []
    picture_idx_per_slide: dict[int, int] = {}

    for item, _ in doc.iterate_items():  # type: ignore[attr-defined]
        if isinstance(item, PictureItem):
            prov = getattr(item, "prov", None)
            slide_num = prov[0].page_no if prov else 0
            idx = picture_idx_per_slide.get(slide_num, 0)
            picture_idx_per_slide[slide_num] = idx + 1

            slide_imgs = slide_images.get(slide_num, [])
            if idx < len(slide_imgs):
                figure_map[item.self_ref] = f"figures/{slide_imgs[idx].name}"
                image_paths.append(slide_imgs[idx])
                item_refs.append(item.self_ref)

    return figure_map, image_paths, item_refs


class PptxConverter(BaseConverter):
    """PPTX conversion: Docling text + python-pptx images + VLM descriptions."""

    def convert(self) -> None:
        from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
        from docling.document_converter import DocumentConverter, PowerpointFormatOption  # noqa: PLC0415

        from tracing import trace_span  # noqa: PLC0415

        self.ensure_output_dir()

        converter = DocumentConverter(
            allowed_formats=[InputFormat.PPTX],
            format_options={InputFormat.PPTX: PowerpointFormatOption()},
        )

        with trace_span("docling.convert_pptx", file=self.source.name):
            logger.info("Converting %s (PPTX native)", self.source.name)
            result = converter.convert(str(self.source))
            logger.info("Status: %s", result.status)

        doc = result.document

        figure_map: dict[str, str] = {}
        figure_descriptions: dict[str, str] = {}
        fig_count = 0

        if self.options.figures:
            slide_images = _extract_pptx_images(self.source, self.output_dir)
            figure_map, image_paths, item_refs = _map_images_to_items(doc, slide_images)
            fig_count = len(image_paths)
            figure_descriptions = self.describe_figures(image_paths, item_refs, "vlm.describe_pptx_images")

        title = extract_title(doc)
        page_md = build_page_annotated_markdown(doc, figure_map, title, figure_descriptions=figure_descriptions)
        self.write_document_md(page_md)

        if fig_count > 0:
            catalog = build_images_catalog(doc, figure_map)
            (self.output_dir / "images.md").write_text(catalog)

        if self.options.all_formats:
            self.write_all_formats(doc)

        if title:
            logger.info("Title: %s", title)
        self.print_summary(
            fig_count=fig_count,
            vlm_used=self.options.vlm and fig_count > 0,
            desc_count=len(figure_descriptions),
        )
