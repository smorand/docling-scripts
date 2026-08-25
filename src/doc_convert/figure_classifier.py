"""Figure classification for the caption filter cascade (Stage B1).

Reuses docling's own ``DocumentFigureClassifier`` (EfficientNet-B0, 26 document
figure categories, ~5M params) to detect purely decorative artwork (logos,
icons, QR codes, ...) so it never reaches a paying captioner. The model already
ships with docling and lands in the shared Hugging Face cache, so this adds no
new dependency and no new download step beyond what docling already pulls.

Measured on Apple Silicon (M-series, MPS): ~7 ms/image at batch 16-32, ~24
ms/image unbatched; ~68 ms/image on CPU at batch 8. Well inside budget.

Everything here is fail-open: a missing model, an offline machine, or any torch
error leaves every figure classified as ``unknown``, so the caller keeps and
captions it exactly as it did before this module existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# Categories that never carry document narrative: skipping them saves a paid
# caption call with no information loss. Everything else, including the
# catch-all "other" (the classifier's least reliable class), is captioned as
# before. Deliberately conservative: this list only grows on evidence.
DECORATIVE_CATEGORIES: frozenset[str] = frozenset(
    {
        "logo",
        "icon",
        "qr_code",
        "bar_code",
        "stamp",
        "signature",
    }
)

# Minimum *combined* probability over DECORATIVE_CATEGORIES before a figure is
# skipped. Thresholding the single top label instead was leaving obvious logos
# captioned, because the classifier splits its mass between `logo` and `icon`
# for the same picture: the IBM DB2 wordmark scored logo 0.35 / icon 0.34, so a
# top-1 rule at 0.8 kept it while the two together say 0.69 "decorative". Summing
# them measures the question we actually ask, "is this decorative", instead of
# "which kind of decorative is it".
#
# 0.80 is set from the 152 figures of a real 98-slide deck, all inspected by eye:
#
#   content misread as decorative   0.490 - 0.658  (a headshot, two editorial
#                                                   illustrations, two 3D renders)
#   vendor logos and pictograms     0.575 - 1.000
#
# So 0.80 clears the highest content item by 0.14. It also strictly contains the
# old top-1 rule (the top label is part of the sum), verified on that deck: 51
# classifier drops became 86 with nothing un-dropped, and all 35 additions were
# confirmed by eye to be pictograms, avatars or vendor logos.
MIN_DECORATIVE_MASS = 0.80

# Used only if docling's model spec cannot be read (API drift).
_FALLBACK_REPO_ID = "docling-project/DocumentFigureClassifier-v2.5"

# Commit we actually benchmarked and validated, pinned so an upstream force-push
# to `main` cannot silently swap the weights under us (CWE-494). It is applied
# only to _FALLBACK_REPO_ID: if a future docling points at a different repo we
# follow whatever revision docling itself declares, because a SHA from another
# repo would 404 and silently disable the filter.
_VALIDATED_REVISION = "f859dfbff5c9916cd996942d4b0db7fa25808220"

_BATCH_SIZE_MPS = 16
_BATCH_SIZE_CPU = 8


@dataclass(frozen=True)
class FigureClass:
    """One classifier verdict for one figure.

    ``label``/``confidence`` are the top prediction, kept for logging and
    diagnostics. ``decorative_mass`` is what the decision uses: the probability
    summed over every decorative category.
    """

    label: str
    confidence: float
    decorative_mass: float = 0.0

    @property
    def is_decorative(self) -> bool:
        """True when this figure can be skipped without losing information."""
        return self.decorative_mass >= MIN_DECORATIVE_MASS


#: Verdict used whenever classification could not run. Never decorative, so a
#: failure always degrades to "caption it", never to "silently drop it".
UNKNOWN = FigureClass(label="unknown", confidence=0.0)


@dataclass
class _Loaded:
    """Lazily loaded classifier state, reused for the whole process."""

    processor: Any
    model: Any
    device: str
    torch: Any


_STATE: _Loaded | None = None
_LOAD_FAILED = False


def _resolve_model_ref() -> tuple[str, str]:
    """Return (repo_id, revision) for the classifier.

    The repo id comes from docling's own model spec, so we stay on the model
    docling ships and has already cached instead of maintaining a second,
    diverging reference. The revision is pinned to a known commit for the
    validated repo; for any other repo we defer to docling's declared revision
    (which may be a branch name, the best available in that case).
    """
    repo_id = _FALLBACK_REPO_ID
    revision = ""
    try:
        from docling.datamodel import stage_model_specs  # noqa: PLC0415

        preset = stage_model_specs.IMAGE_CLASSIFICATION_DOCUMENT_FIGURE
        spec_repo = preset.model_spec.get_repo_id(preset.default_engine_type)
        spec_rev = preset.model_spec.get_revision(preset.default_engine_type)
        if isinstance(spec_repo, str) and spec_repo:
            repo_id = spec_repo
        if isinstance(spec_rev, str) and spec_rev:
            revision = spec_rev
    except Exception:  # pragma: no cover - defensive against docling API drift
        logger.debug("Could not read figure classifier spec from docling, using fallback reference")

    if repo_id == _FALLBACK_REPO_ID:
        revision = _VALIDATED_REVISION
    return repo_id, revision or "main"


def _load() -> _Loaded | None:
    """Load (and cache) the classifier, or return None if unavailable.

    Lazy by design: the model is fetched on first real use, not at CLI start.
    If it is not in the Hugging Face cache and the machine is offline, this
    returns None and the whole cascade degrades to Stage A (size floor) only.
    """
    global _STATE, _LOAD_FAILED  # noqa: PLW0603

    if _STATE is not None:
        return _STATE
    if _LOAD_FAILED:
        return None

    try:
        import torch  # noqa: PLC0415
        from transformers import AutoImageProcessor, AutoModelForImageClassification  # noqa: PLC0415

        repo_id, revision = _resolve_model_ref()
        processor = AutoImageProcessor.from_pretrained(  # type: ignore[no-untyped-call]
            repo_id, revision=revision
        )
        model = AutoModelForImageClassification.from_pretrained(repo_id, revision=revision)
        model.eval()
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model.to(device)
        _STATE = _Loaded(processor=processor, model=model, device=device, torch=torch)
    except Exception as exc:
        logger.warning(
            "Caption filter: figure classifier unavailable (%s); every figure will be captioned. "
            "Run doc-convert --download-models once with network access to enable it.",
            exc,
        )
        _LOAD_FAILED = True
        return None

    logger.debug("Figure classifier ready on %s", _STATE.device)
    return _STATE


def _forward(state: _Loaded, images: list[Any]) -> list[FigureClass]:
    """Run one batch through the model, retrying on CPU after any MPS failure.

    The CPU retry is deliberately broader than the float64-only check used by
    the docling pipelines (``vlm.is_mps_float64_error``): there, a lost retry
    surfaces as a failed conversion, here it would silently disable the filter
    for the rest of the run. One extra attempt is cheap; losing the feature to
    an unrecognised MPS quirk is not. The device switch sticks for the process
    so a broken accelerator is not retried on every batch.
    """
    torch = state.torch
    batch = state.processor(images=images, return_tensors="pt")

    def run(device: str) -> Any:
        moved = {key: value.to(device) for key, value in batch.items()}
        with torch.no_grad():
            return state.model(**moved).logits

    try:
        logits = run(state.device)
    except Exception as exc:
        if state.device != "mps":
            raise
        logger.warning("Figure classifier failed on MPS, falling back to CPU for this run: %s", exc)
        state.model.to("cpu")
        state.device = "cpu"
        logits = run("cpu")

    probs = logits.float().softmax(dim=-1)
    confidences, indices = probs.max(dim=-1)
    id2label = state.model.config.id2label
    decorative_columns = [i for i, label in id2label.items() if str(label) in DECORATIVE_CATEGORIES]
    masses = probs[:, decorative_columns].sum(dim=-1)
    return [
        FigureClass(label=str(id2label[int(idx)]), confidence=float(conf), decorative_mass=float(mass))
        for idx, conf, mass in zip(indices.tolist(), confidences.tolist(), masses.tolist(), strict=True)
    ]


def _classify_pil_batch(state: _Loaded, images: list[Any]) -> list[FigureClass]:
    """Classify one chunk of already-decoded RGB images; failures stay UNKNOWN."""
    if not images:
        return []

    try:
        return _forward(state, images)
    except Exception as exc:
        logger.warning("Figure classifier failed on a batch of %d image(s), keeping them: %s", len(images), exc)
        return [UNKNOWN] * len(images)


def classify_pil_images(images: list[Any]) -> list[FigureClass]:
    """Classify already-decoded images (e.g. in-memory PDF crops), same order.

    Shares the model/device/batching with :func:`classify_figures` but skips the
    file round-trip: callers that already hold pixels (the LLM OCR engine crops
    bitmap regions straight out of a PDF page) do not have to write a temporary
    PNG just to satisfy a path-shaped signature. Never raises: on any failure
    the corresponding entry is ``UNKNOWN``, which is never decorative, so the
    caller keeps and sends the region as before this filter existed.
    """
    if not images:
        return []

    state = _load()
    if state is None:
        return [UNKNOWN] * len(images)

    rgb_images: list[Any] = []
    for image in images:
        try:
            rgb_images.append(image.convert("RGB"))
        except Exception:
            logger.warning("Figure classifier: could not convert an in-memory image, keeping the figure")
            rgb_images.append(None)

    batch_size = _BATCH_SIZE_MPS if state.device == "mps" else _BATCH_SIZE_CPU
    verdicts: list[FigureClass] = [UNKNOWN] * len(rgb_images)
    positions = [i for i, img in enumerate(rgb_images) if img is not None]
    to_classify = [rgb_images[i] for i in positions]

    predictions: list[FigureClass] = []
    for start in range(0, len(to_classify), batch_size):
        predictions.extend(_classify_pil_batch(state, to_classify[start : start + batch_size]))

    for position, prediction in zip(positions, predictions, strict=True):
        verdicts[position] = prediction
    return verdicts


def _classify_batch(state: _Loaded, paths: list[Path]) -> list[FigureClass]:
    """Classify one chunk of images; unreadable or failing images stay UNKNOWN."""
    from PIL import Image  # noqa: PLC0415

    verdicts: list[FigureClass] = [UNKNOWN] * len(paths)
    images: list[Any] = []
    positions: list[int] = []

    for position, path in enumerate(paths):
        try:
            with Image.open(path) as img:
                images.append(img.convert("RGB"))
            positions.append(position)
        except Exception:
            logger.warning("Figure classifier: could not read %s, keeping the figure", path)

    if not images:
        return verdicts

    try:
        predictions = _forward(state, images)
    except Exception as exc:
        logger.warning("Figure classifier failed on a batch of %d image(s), keeping them: %s", len(images), exc)
        return verdicts

    for position, prediction in zip(positions, predictions, strict=True):
        verdicts[position] = prediction
    return verdicts


def classify_figures(paths: list[Path]) -> list[FigureClass]:
    """Classify every figure in ``paths``, one verdict per input, same order.

    Never raises: on any failure the corresponding entry is ``UNKNOWN``, which
    is never decorative, so the caller keeps the figure.
    """
    if not paths:
        return []

    state = _load()
    if state is None:
        return [UNKNOWN] * len(paths)

    batch_size = _BATCH_SIZE_MPS if state.device == "mps" else _BATCH_SIZE_CPU
    verdicts: list[FigureClass] = []
    for start in range(0, len(paths), batch_size):
        verdicts.extend(_classify_batch(state, paths[start : start + batch_size]))
    return verdicts


def prefetch() -> bool:
    """Warm the classifier into the Hugging Face cache; True if it is usable.

    Used by ``--download-models`` so a machine can be prepared before going
    offline. Runtime does not need this: the model is lazy-loaded on first use.
    Deliberately goes through the same ``_load`` path as runtime so a successful
    prefetch proves the runtime path works too.
    """
    return _load() is not None


def reset_cache() -> None:
    """Drop the cached model (tests only)."""
    global _STATE, _LOAD_FAILED  # noqa: PLW0603

    _STATE = None
    _LOAD_FAILED = False
