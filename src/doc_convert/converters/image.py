"""Image and PDF-via-external-LLM converter."""

from __future__ import annotations

import logging
from pathlib import Path  # noqa: TC003

from config import Settings  # noqa: TC001
from doc_convert.base import BaseConverter

logger = logging.getLogger(__name__)


def convert_image_to_markdown(
    doc_path: Path,
    provider: str,
    model: str,
    settings: Settings,
    *,
    prompt: str | None = None,
) -> str:
    """Convert a single image or PDF via external LLM VlmPipeline. Returns markdown.

    Pass ``prompt`` to override the default whole-page conversion prompt
    (e.g. for single-figure captioning).

    Images exceeding the API size limit (5 MB) are automatically recompressed
    to JPEG and downscaled before being sent.  PDFs are passed through as-is.
    """
    from docling.datamodel.base_models import InputFormat  # noqa: PLC0415
    from docling.datamodel.pipeline_options import VlmPipelineOptions  # noqa: PLC0415
    from docling.datamodel.pipeline_options_vlm_model import ApiVlmOptions, ResponseFormat  # noqa: PLC0415
    from docling.document_converter import (  # noqa: PLC0415
        DocumentConverter,
        FormatOption,
        ImageFormatOption,
        PdfFormatOption,
    )
    from docling.pipeline.vlm_pipeline import VlmPipeline  # noqa: PLC0415

    from doc_convert.image_prep import (  # noqa: PLC0415
        MAX_IMAGE_BYTES,
        ensure_image_under_limit,
        install_docling_image_size_patch,
    )
    from doc_convert.providers import get_external_llm_prompt, get_provider_url, require_api_key  # noqa: PLC0415
    from tracing import trace_span  # noqa: PLC0415

    install_docling_image_size_patch()

    api_key = require_api_key(provider, settings)
    vlm_opts = ApiVlmOptions(
        url=get_provider_url(provider, settings),
        params={"model": model, "max_tokens": settings.llm_max_tokens},
        headers={"Authorization": f"Bearer {api_key}"},
        prompt=prompt or get_external_llm_prompt(),
        scale=2.0,
        timeout=settings.llm_timeout,
        response_format=ResponseFormat.MARKDOWN,
        temperature=0.0,
    )
    pipeline_options = VlmPipelineOptions(enable_remote_services=True, vlm_options=vlm_opts)

    ext = doc_path.suffix.lower()
    if ext == ".pdf":
        fmt = InputFormat.PDF
        format_options: dict[InputFormat, FormatOption] = {
            InputFormat.PDF: PdfFormatOption(pipeline_cls=VlmPipeline, pipeline_options=pipeline_options)
        }
        converter = DocumentConverter(allowed_formats=[fmt], format_options=format_options)
        with trace_span("docling.convert_external_llm", file=doc_path.name, provider=provider, model=model):
            logger.info("Converting %s (%s/%s)", doc_path.name, provider, model)
            result = converter.convert(str(doc_path))
            logger.info("Status: %s", result.status)
        return result.document.export_to_markdown()

    # Non-PDF: ensure the image payload fits the provider's size limit.
    fmt = InputFormat.IMAGE
    format_options = {InputFormat.IMAGE: ImageFormatOption(pipeline_cls=VlmPipeline, pipeline_options=pipeline_options)}
    converter = DocumentConverter(allowed_formats=[fmt], format_options=format_options)

    # Docling's VlmPipeline applies scale=2.0 which quadruples the pixel count
    # before encoding as PNG RGBA. Pre-cap image dimensions so the PNG stays < 5 MB.
    # We cap at MAX_IMAGE_BYTES / (scale^2 * 4 bytes/px) -- worst case: PNG is
    # uncompressed RGBA. In practice PNG compresses well so the limit is generous.
    _DOCLING_SCALE = 2.0
    _max_pixels_for_scale = int(MAX_IMAGE_BYTES / (_DOCLING_SCALE**2 * 4))

    with ensure_image_under_limit(doc_path, max_pixels=_max_pixels_for_scale) as prepared:
        with trace_span("docling.convert_external_llm", file=doc_path.name, provider=provider, model=model):
            logger.info("Converting %s (%s/%s)", doc_path.name, provider, model)
            result = converter.convert(str(prepared.path))
            logger.info("Status: %s", result.status)
        return result.document.export_to_markdown()


class ImageConverter(BaseConverter):
    """Image/PDF conversion via external LLM VlmPipeline."""

    def convert(self) -> None:
        import typer  # noqa: PLC0415

        from logging_config import console  # noqa: PLC0415

        if not self.options.llm:
            console.print("[red]Image and PDF --engine llm conversion requires --llm <provider/model>[/red]")
            raise typer.Exit(1)

        from doc_convert.providers import parse_external_llm  # noqa: PLC0415

        provider, model = parse_external_llm(self.options.llm)
        md = convert_image_to_markdown(self.source, provider, model, self.options.settings)
        if not md.strip():
            console.print(
                f"[red]Conversion produced no text for {self.source.name} "
                f"({provider}/{model}). The model may be unavailable or returned "
                "an error. Re-run with -vv to see the API response.[/red]"
            )
            raise typer.Exit(1)
        self.ensure_output_dir()
        self.write_document_md(md)
        self.print_summary()
