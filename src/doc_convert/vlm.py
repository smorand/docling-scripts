"""VLM image description pipelines (local and external)."""

from __future__ import annotations

import logging
import re
from pathlib import Path  # noqa: TC003

import typer

from config import Settings  # noqa: TC001
from doc_convert.providers import DEFAULT_LLM_CONCURRENCY
from logging_config import console

logger = logging.getLogger(__name__)


def is_mps_float64_error(exc: BaseException) -> bool:
    """Return True if the exception looks like the Apple Silicon MPS float64 incompatibility."""
    msg = str(exc).lower()
    return "mps" in msg and ("float64" in msg or "doesn't support" in msg)


def _build_local_vlm_model(vlm_preset: str, models_path: Path, prompt: str, *, cpu: bool):  # type: ignore[no-untyped-def]
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions  # noqa: PLC0415
    from docling.datamodel.pipeline_options import PictureDescriptionVlmEngineOptions  # noqa: PLC0415
    from docling.models.stages.picture_description.picture_description_vlm_engine_model import (  # noqa: PLC0415
        PictureDescriptionVlmEngineModel,
    )

    options = PictureDescriptionVlmEngineOptions.from_preset(vlm_preset)
    # Override the preset's default short caption prompt with the supplied one.
    try:
        options.prompt = prompt
    except (AttributeError, TypeError):
        logger.debug("Local captioner preset %s does not accept a prompt override", vlm_preset)
    accel = AcceleratorOptions(device=AcceleratorDevice.CPU) if cpu else AcceleratorOptions()
    return PictureDescriptionVlmEngineModel(
        enabled=True,
        enable_remote_services=False,
        artifacts_path=str(models_path),
        options=options,
        accelerator_options=accel,
    )


def describe_images_with_vlm(
    image_paths: list[Path],
    vlm_preset: str,
    models_path: Path,
    *,
    prompt: str | None = None,
) -> list[str]:
    """Describe images using local VLM engine (offline, uses models_path).

    Per-image context is intentionally not supported here: the local pipeline
    sets one prompt per model instance, so honouring per-image context would
    rebuild the model for every image.

    Automatically retries on CPU when an Apple Silicon MPS float64 error is detected.
    """
    from PIL import Image  # noqa: PLC0415

    from doc_convert.providers import get_caption_prompt  # noqa: PLC0415

    effective_prompt = prompt or get_caption_prompt()

    if not models_path.exists():
        console.print(f"[red]Models directory not found: {models_path}[/red]")
        console.print("Run [bold]doc-convert --download-models[/bold] first.")
        raise typer.Exit(1)

    # Filter out unsupported image formats (WMF, EMF) that PIL cannot open on macOS
    UNSUPPORTED_EXTS = {".wmf", ".emf"}
    valid_indices: list[int] = []
    pil_images: list[Image.Image] = []
    for i, p in enumerate(image_paths):
        if p.suffix.lower() in UNSUPPORTED_EXTS:
            logger.warning("Skipping unsupported image format: %s", p.name)
            continue
        try:
            pil_images.append(Image.open(p))
            valid_indices.append(i)
        except Exception:
            logger.warning("Failed to open image: %s", p.name)

    logger.info("Describing %d/%d image(s) with local VLM (%s)", len(pil_images), len(image_paths), vlm_preset)

    if not pil_images:
        return [""] * len(image_paths)

    try:
        model = _build_local_vlm_model(vlm_preset, models_path, effective_prompt, cpu=False)
        raw = list(model._annotate_images(pil_images))
    except RuntimeError as exc:
        if not is_mps_float64_error(exc):
            raise
        logger.warning("MPS float64 error from local captioner; retrying on CPU")
        model = _build_local_vlm_model(vlm_preset, models_path, effective_prompt, cpu=True)
        raw = list(model._annotate_images(pil_images))
    cleaned = [re.sub(r"<end_of_utteranc\w*>?", "", d).strip() for d in raw]

    results: list[str] = [""] * len(image_paths)
    for idx, desc in zip(valid_indices, cleaned, strict=True):
        results[idx] = desc
    return results


def describe_images_with_external_llm(
    image_paths: list[Path],
    provider: str,
    model: str,
    settings: Settings,
    *,
    prompt: str | None = None,
    contexts: list[str] | None = None,
    concurrency: int = DEFAULT_LLM_CONCURRENCY,
) -> list[str]:
    """Describe figures/tables with a cloud LLM, ``concurrency`` at a time.

    ``prompt`` defaults to the figure caption prompt. ``contexts`` is an
    optional per-image context block (caption + body mention) that is
    prepended to the prompt; pass an empty string for items without context.

    Each image is one independent HTTPS call, so this fans out over threads
    (see ``vision_llm``). It deliberately does *not* go through docling's
    VlmPipeline: that abstraction rebuilt a DocumentConverter per image, forced
    sequential execution, and upscaled every figure 2x for no measured benefit.
    An image that fails every retry yields "" and stays undescribed rather than
    aborting the document.
    """
    from doc_convert.providers import get_caption_prompt, require_api_key  # noqa: PLC0415
    from doc_convert.vision_llm import describe_image, make_client, map_concurrent  # noqa: PLC0415

    base_prompt = prompt or get_caption_prompt()
    contexts = contexts or [""] * len(image_paths)
    if len(contexts) != len(image_paths):
        raise ValueError("contexts length must match image_paths length")
    if not image_paths:
        return []

    api_key = require_api_key(provider, settings)
    total = len(image_paths)
    workers = max(1, min(concurrency, total))
    logger.info("Describing %d image(s) with %s/%s (%d at a time)", total, provider, model, workers)

    jobs = list(zip(image_paths, contexts, strict=True))

    def report(done: int, job: tuple[Path, str], description: str, elapsed: float) -> None:
        img_path, _ctx = job
        if description:
            logger.info("Described %s in %.1fs (%d/%d done)", img_path.name, elapsed, done, total)
        else:
            logger.warning("No description for %s (%d/%d done)", img_path.name, done, total)

    with make_client(settings, workers) as client:

        def work(job: tuple[Path, str]) -> str:
            img_path, ctx = job
            full_prompt = f"{ctx}{base_prompt}" if ctx else base_prompt
            return describe_image(img_path, full_prompt, provider, model, settings, api_key, client).strip()

        return map_concurrent(jobs, work, workers, what="figure caption", on_done=report)
