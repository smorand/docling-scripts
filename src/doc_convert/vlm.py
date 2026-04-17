"""VLM image description pipelines (local and external)."""

from __future__ import annotations

import logging
import re
from pathlib import Path  # noqa: TC003

import typer

from config import Settings  # noqa: TC001
from logging_config import console

logger = logging.getLogger(__name__)


def describe_images_with_vlm(image_paths: list[Path], vlm_preset: str, models_path: Path) -> list[str]:
    """Describe images using local VLM engine (offline, uses models_path)."""
    from docling.datamodel.accelerator_options import AcceleratorOptions  # noqa: PLC0415
    from docling.datamodel.pipeline_options import PictureDescriptionVlmEngineOptions  # noqa: PLC0415
    from docling.models.stages.picture_description.picture_description_vlm_engine_model import (  # noqa: PLC0415
        PictureDescriptionVlmEngineModel,
    )
    from PIL import Image  # noqa: PLC0415

    if not models_path.exists():
        console.print(f"[red]Models directory not found: {models_path}[/red]")
        console.print("Run [bold]doc-convert --download-models[/bold] first.")
        raise typer.Exit(1)

    options = PictureDescriptionVlmEngineOptions.from_preset(vlm_preset)
    model = PictureDescriptionVlmEngineModel(
        enabled=True,
        enable_remote_services=False,
        artifacts_path=str(models_path),
        options=options,
        accelerator_options=AcceleratorOptions(),
    )

    logger.info("Describing %d image(s) with local VLM (%s)", len(image_paths), vlm_preset)
    pil_images = [Image.open(p) for p in image_paths]
    raw = list(model._annotate_images(pil_images))
    return [re.sub(r"<end_of_utteranc\w*>?", "", d).strip() for d in raw]


def describe_images_with_external_llm(
    image_paths: list[Path], provider: str, model: str, settings: Settings
) -> list[str]:
    """Describe images using an external LLM provider."""
    from doc_convert.converters.image import convert_image_to_markdown  # noqa: PLC0415

    logger.info("Describing %d image(s) with %s/%s", len(image_paths), provider, model)
    descriptions: list[str] = []
    for img_path in image_paths:
        md = convert_image_to_markdown(img_path, provider, model, settings)
        descriptions.append(md.strip())
    return descriptions
