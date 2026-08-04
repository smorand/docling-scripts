"""Image pre-processing: ensure images are under the API size limit.

Two entry points:

1. ``ensure_image_under_limit(path)`` -- file-based guard for images we send
   ourselves (pptx_slide_vlm, companion). Returns a ``PreparedImage`` context
   manager; the tmp JPEG is auto-deleted on exit.

2. ``install_docling_image_size_patch()`` -- monkey-patches
   ``docling.utils.api_image_request.api_image_request`` so that every PIL
   image Docling tries to encode as PNG is transparently compressed to JPEG
   when it would exceed the limit. Call once at startup (idempotent).

Both apply the same strategy: JPEG quality 90 -> 20 (step 10), then halve
dimensions up to 4 times until the payload fits 5 MB.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

logger = logging.getLogger(__name__)

# 5 MB hard limit (Bedrock / IBM ICA / most providers).  Keep 200 KB margin.
MAX_IMAGE_BYTES: int = 5 * 1024 * 1024 - 200 * 1024  # 4 996 352 bytes

# Maximum side length (px) before downscaling.  Avoids sending unnecessarily
# large images to cloud APIs while preserving enough detail for VLM reading.
MAX_IMAGE_SIDE: int = 5000

# JPEG quality steps: start high, drop by 10 % each round.
_QUALITY_START = 90
_QUALITY_STEP = 10
_QUALITY_MIN = 20

# Formats that PIL saves as JPEG without mode-conversion issues.
_JPEG_SAFE_MODES = {"RGB", "L"}

# Extensions already in a compact format that are usually small — skip
# conversion if they fit.
_SKIP_CONVERT_EXTS = {".jpg", ".jpeg"}


class PreparedImage:
    """Wraps the path to a (possibly recompressed) image.

    Use as a context manager to auto-delete the tmp file::

        with ensure_image_under_limit(src) as prepared:
            do_something(prepared.path)

    Or call ``.cleanup()`` explicitly.  When the original was already within
    the limit and no tmp file was created, cleanup is a no-op.
    """

    def __init__(self, path: Path, *, is_tmp: bool) -> None:
        self.path = path
        self._is_tmp = is_tmp

    # --- context-manager protocol ---

    def __enter__(self) -> PreparedImage:
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()

    # --- public helpers ---

    def cleanup(self) -> None:
        if self._is_tmp:
            with contextlib.suppress(OSError):
                self.path.unlink(missing_ok=True)
            self._is_tmp = False  # guard against double-cleanup


def ensure_image_under_limit(
    src: Path,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
    max_pixels: int | None = None,
    max_side: int | None = MAX_IMAGE_SIDE,
) -> PreparedImage:
    """Return a ``PreparedImage`` whose file is guaranteed to be <= ``max_bytes``.

    Optionally caps dimensions before any size check:
    - ``max_side``: downscale so the longest side <= max_side (default 5000 px).
    - ``max_pixels``: downscale to fit within total pixel count (useful when
      the image will be upscaled internally, e.g. Docling scale=2.0).
    Both are applied in order (max_side first, then max_pixels on the result).

    Strategy:
    1. If max_side is set, downscale so longest side <= max_side.
    2. If max_pixels is set, downscale to fit within max_pixels.
    3. If the (possibly downscaled) file already fits max_bytes, return unchanged.
    4. Convert to JPEG (RGB) at quality ``_QUALITY_START``.
    5. If still too large, reduce quality by ``_QUALITY_STEP`` until the image
       fits or quality reaches ``_QUALITY_MIN``.
    6. If even the minimum quality exceeds the limit, halve the image dimensions
       and retry the quality loop (may repeat up to 3 times).

    Logs a warning whenever any compression or downscale is applied.
    """
    from PIL import Image as _PIL  # noqa: PLC0415

    # Step 0a: cap longest side if requested.
    if max_side is not None:
        with _PIL.open(src) as _probe:
            w, h = _probe.size
        if max(w, h) > max_side:
            ratio = max_side / max(w, h)
            new_w = max(1, int(w * ratio))
            new_h = max(1, int(h * ratio))
            logger.warning(
                "Image %s is %dx%d -- downscaling to %dx%d (max side %d px)",
                src.name, w, h, new_w, new_h, max_side,
            )
            img_full = _open_as_rgb(src)
            img_resized = img_full.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buf = _encode_jpeg(img_resized, _QUALITY_START)
            src = _write_tmp(buf).path  # type: ignore[assignment]

    # Step 0b: cap pixel count if requested (e.g. Docling scale=2.0 upscales
    # quadruples pixel count before PNG encoding).
    if max_pixels is not None:
        with _PIL.open(src) as _probe:
            w, h = _probe.size
        if w * h > max_pixels:
            ratio = (max_pixels / (w * h)) ** 0.5
            new_w = max(1, int(w * ratio))
            new_h = max(1, int(h * ratio))
            logger.warning(
                "Image %s is %dx%d (%d px) -- downscaling to %dx%d to stay under pixel cap",
                src.name, w, h, w * h, new_w, new_h,
            )
            img_full = _open_as_rgb(src)
            img_resized = img_full.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buf = _encode_jpeg(img_resized, _QUALITY_START)
            return _write_tmp(buf)

    size = src.stat().st_size

    if size <= max_bytes:
        # Already fine -- skip all processing.
        return PreparedImage(src, is_tmp=False)

    logger.warning(
        "Image %s is %.1f MB (limit %.1f MB) -- compressing to JPEG before API call",
        src.name,
        size / 1024 / 1024,
        max_bytes / 1024 / 1024,
    )

    img: PILImage = _open_as_rgb(src)
    original_size = img.size

    for downscale_round in range(4):  # at most 4 halvings
        for quality in range(_QUALITY_START, _QUALITY_MIN - 1, -_QUALITY_STEP):
            buf = _encode_jpeg(img, quality)
            if len(buf) <= max_bytes:
                if downscale_round > 0 or quality < _QUALITY_START:
                    logger.warning(
                        "  -> compressed to %.1f MB (quality=%d%s)",
                        len(buf) / 1024 / 1024,
                        quality,
                        f", downscaled x{2**downscale_round}" if downscale_round > 0 else "",
                    )
                return _write_tmp(buf)

        # Quality loop exhausted — halve dimensions.
        new_w = max(1, img.size[0] // 2)
        new_h = max(1, img.size[1] // 2)
        logger.warning(
            "  → still too large at min quality; downscaling %dx%d → %dx%d",
            img.size[0],
            img.size[1],
            new_w,
            new_h,
        )
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Last resort: write whatever we have at minimum quality.
    buf = _encode_jpeg(img, _QUALITY_MIN)
    logger.warning(
        "  → forced final size %.1f MB (could not reach limit; original was %.1f MB, "
        "final resolution %dx%d vs original %dx%d)",
        len(buf) / 1024 / 1024,
        size / 1024 / 1024,
        img.size[0],
        img.size[1],
        original_size[0],
        original_size[1],
    )
    return _write_tmp(buf)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _open_as_rgb(path: Path) -> PILImage:
    result: PILImage = Image.open(path)
    if result.mode not in _JPEG_SAFE_MODES:
        result = result.convert("RGB")
    return result


def _encode_jpeg(img: PILImage, quality: int) -> bytes:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _write_tmp(data: bytes) -> PreparedImage:
    fd, tmp_path_str = tempfile.mkstemp(suffix=".jpg")
    tmp_path = Path(tmp_path_str)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return PreparedImage(tmp_path, is_tmp=True)


# ---------------------------------------------------------------------------
# PIL-level compression (for in-memory images, e.g. from Docling internals)
# ---------------------------------------------------------------------------


def compress_pil_image_for_api(
    img: PILImage,
    *,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> bytes:
    """Return JPEG bytes for *img* that are guaranteed to be <= *max_bytes*.

    Used by the Docling monkey-patch: Docling rasterises pages at scale=2.0
    which can produce very large PIL images; this compresses them before
    base64-encoding for the API call.

    Returns JPEG bytes. Logs a warning whenever compression or downscale fires.
    """

    # Quick check: encode as PNG first to see if it already fits.
    png_buf = BytesIO()
    safe = img if img.mode in _JPEG_SAFE_MODES else img.convert("RGB")
    safe.save(png_buf, format="PNG")
    png_size = len(png_buf.getvalue())

    if png_size <= max_bytes:
        return png_buf.getvalue()  # already fine as PNG

    logger.warning(
        "Image rasterised by Docling is %.1f MB (limit %.1f MB) -- compressing to JPEG",
        png_size / 1024 / 1024,
        max_bytes / 1024 / 1024,
    )

    work = safe
    original_size = work.size

    for downscale_round in range(4):
        for quality in range(_QUALITY_START, _QUALITY_MIN - 1, -_QUALITY_STEP):
            buf = _encode_jpeg(work, quality)
            if len(buf) <= max_bytes:
                if downscale_round > 0 or quality < _QUALITY_START:
                    logger.warning(
                        "  -> compressed to %.1f MB (quality=%d%s)",
                        len(buf) / 1024 / 1024,
                        quality,
                        f", downscaled x{2**downscale_round}" if downscale_round > 0 else "",
                    )
                return buf

        new_w = max(1, work.size[0] // 2)
        new_h = max(1, work.size[1] // 2)
        logger.warning(
            "  -> still too large at min quality; downscaling %dx%d -> %dx%d",
            work.size[0],
            work.size[1],
            new_w,
            new_h,
        )
        work = work.resize((new_w, new_h), Image.Resampling.LANCZOS)

    buf = _encode_jpeg(work, _QUALITY_MIN)
    logger.warning(
        "  -> forced final size %.1f MB (original PNG was %.1f MB, final resolution %dx%d vs original %dx%d)",
        len(buf) / 1024 / 1024,
        png_size / 1024 / 1024,
        work.size[0],
        work.size[1],
        original_size[0],
        original_size[1],
    )
    return buf


# ---------------------------------------------------------------------------
# Docling monkey-patch
# ---------------------------------------------------------------------------

_DOCLING_PATCHED = False


def install_docling_image_size_patch() -> None:
    """Monkey-patch docling's api_image_request to enforce the 5 MB limit.

    Docling rasterises images at scale=2.0 then encodes them as PNG in RAM
    before sending; this can easily exceed 5 MB even when the source file is
    small. The patch intercepts the PIL image just before encoding: if the
    PNG would exceed the limit, it encodes as JPEG instead and replaces the
    data: URL mime type accordingly.

    Idempotent: safe to call multiple times.
    """
    global _DOCLING_PATCHED  # noqa: PLW0603
    if _DOCLING_PATCHED:
        return

    try:
        # Pre-import the modules that hold direct references so sys.modules has them.
        import docling.models.inference_engines.vlm.api_openai_compatible_engine  # noqa: PLC0415
        import docling.models.stages.picture_description.picture_description_api_model  # noqa: PLC0415
        import docling.models.vlm_pipeline_models.api_vlm_model  # noqa: PLC0415, F401
        import docling.utils.api_image_request as _mod  # noqa: PLC0415
    except ImportError:
        logger.debug("Docling not available; skipping api_image_request patch")
        return

    _orig = _mod.api_image_request

    def _patched_api_image_request(
        image: object,
        prompt: str,
        url: object,
        timeout: float = 20,
        headers: dict[str, str] | None = None,
        **kwargs: object,
    ) -> object:
        from io import BytesIO as _BytesIO  # noqa: PLC0415

        from PIL import Image as _PILMod  # noqa: PLC0415

        if not isinstance(image, _PILMod.Image):
            return _orig(image, prompt, url, timeout=timeout, headers=headers, **kwargs)  # type: ignore[arg-type]

        # Probe PNG size: simulate what _orig will do (copy -> RGBA -> PNG).
        probe_buf = _BytesIO()
        image.copy().convert("RGBA").save(probe_buf, "PNG")
        if len(probe_buf.getvalue()) <= MAX_IMAGE_BYTES:
            # Already fine; let original handle it.
            return _orig(image, prompt, url, timeout=timeout, headers=headers, **kwargs)  # type: ignore[arg-type]

        # PNG too large. Compress iteratively until the RGBA PNG that _orig will
        # produce is also under the limit.
        probe = image.copy().convert("RGB")
        work = probe
        _VERIFY_LIMIT = MAX_IMAGE_BYTES
        for downscale_round in range(4):
            for quality in range(_QUALITY_START, _QUALITY_MIN - 1, -_QUALITY_STEP):
                jpeg_bytes = _encode_jpeg(work, quality)
                # Simulate what _orig will do: open JPEG, convert RGBA, save PNG.
                candidate = _PILMod.open(_BytesIO(jpeg_bytes)).convert("RGBA")
                verify_buf = _BytesIO()
                candidate.save(verify_buf, "PNG")
                if len(verify_buf.getvalue()) <= _VERIFY_LIMIT:
                    logger.warning(
                        "  -> compressed to %.1f MB PNG via quality=%d%s",
                        len(verify_buf.getvalue()) / 1024 / 1024,
                        quality,
                        f", downscaled x{2**downscale_round}" if downscale_round > 0 else "",
                    )
                    small_img = _PILMod.open(_BytesIO(jpeg_bytes)).convert("RGB")
                    return _orig(small_img, prompt, url, timeout=timeout, headers=headers, **kwargs)  # type: ignore[arg-type]

            # All qualities exhausted; halve dimensions.
            new_w = max(1, work.size[0] // 2)
            new_h = max(1, work.size[1] // 2)
            logger.warning(
                "  -> still too large; downscaling %dx%d -> %dx%d",
                work.size[0],
                work.size[1],
                new_w,
                new_h,
            )
            work = work.resize((new_w, new_h), _PILMod.Resampling.LANCZOS)

        # Last resort: pass the smallest work image to _orig.
        small_img = work
        return _orig(small_img, prompt, url, timeout=timeout, headers=headers, **kwargs)  # type: ignore[arg-type]

    # Patch all known docling modules that hold a direct reference to api_image_request.
    _MODULES_TO_PATCH = (
        "docling.utils.api_image_request",
        "docling.models.inference_engines.vlm.api_openai_compatible_engine",
        "docling.models.vlm_pipeline_models.api_vlm_model",  # legacy ApiVlmModel path
        "docling.models.stages.picture_description.picture_description_api_model",
    )
    import sys  # noqa: PLC0415

    for _mod_name in _MODULES_TO_PATCH:
        _target = sys.modules.get(_mod_name)
        if _target is not None and hasattr(_target, "api_image_request"):
            _target.api_image_request = _patched_api_image_request  # type: ignore[attr-defined]

    _mod.api_image_request = _patched_api_image_request  # type: ignore[assignment]

    _DOCLING_PATCHED = True
    logger.debug("Installed Docling api_image_request size-limit patch")
