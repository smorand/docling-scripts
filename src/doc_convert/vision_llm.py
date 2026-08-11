"""One image plus one prompt gives text: the shared vision-LLM primitive.

Three call sites need exactly this: figure captions, table descriptions, and PPTX
slide-screenshot analysis. Two of them used to reach it through docling's
``VlmPipeline``, which is a *document conversion* abstraction. Measured cost of
that detour on a 98-slide deck with 99 figures:

  - 100 ``DocumentConverter`` instances built for 66 distinct option hashes,
    because the per-image context block is part of the prompt and the prompt is
    part of docling's pipeline cache key, so the cache never hits: ~145 s (16%)
    of the caption phase.
  - ``scale=2.0`` upscaling before send, which costs up to +74% image tokens on
    small figures and 5.4x the payload bytes on large ones (the provider caps
    image tokens around 1750 and downsamples anyway), and measurably *hurts*
    fine reading: on a bar chart whose true value is 904, the native image was
    read as 900 and the 2x upscale as 950.
  - A ``markdown -> DoclingDocument -> markdown`` round trip that was verified
    byte-identical on all 99 captions of that deck, so it contributes nothing.
  - Sequential execution, since each call is a separate document conversion.

Everything here is plain ``httpx``: the client is documented thread-safe and the
work is pure I/O, so callers can fan out (see :func:`map_concurrent`). Requests
that fail transiently are retried; a wrong model slug fails fast.
"""

from __future__ import annotations

import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

import httpx
import typer

from config import Settings  # noqa: TC001
from doc_convert.providers import (
    RETRY_BACKOFF_SECONDS,
    RETRYABLE_HTTP_STATUS,
    get_provider_url,
)
from logging_config import console

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger(__name__)

# HTTP status meaning "model slug not found/served" on the OpenAI-compatible endpoint.
_HTTP_NOT_FOUND = 404

# Attempts per image, shared by every caller. Concurrency makes rate limits more
# likely, and a dropped image means a missing description in document.md.
MAX_ATTEMPTS = 4

# Captions are deterministic on purpose: docling used to send temperature 0.0 and
# a re-run should not reshuffle wording for the same picture.
_TEMPERATURE = 0.0

T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class VisionAttempt:
    """Outcome of one vision call.

    ``retryable`` separates "the provider hiccuped" from "this request is wrong",
    so a 400 does not burn the backoff schedule and a 429 is not mistaken for a
    permanent failure.
    """

    text: str = ""
    retryable: bool = False
    reason: str = ""


def encode_image(image_path: Path) -> tuple[str, str]:
    """Return ``(mime, base64)`` for an image, shrunk first if it exceeds the API limit.

    The provider's ceiling applies to the **base64 payload**, which is 4/3 the
    size of the bytes on disk. ``MAX_IMAGE_BYTES`` is a file-size budget, so it
    is scaled by 3/4 here. Without that, a 4.5 MB PNG passes the guard untouched
    and then gets rejected: measured on a real 3072x1728 figure, 4.54 MB raw is
    6.05 MB of base64 and the API answered 400, silently costing that figure its
    caption. At 3/4 the same image goes through at 0.86 MB and answers 200.
    """
    from doc_convert.image_prep import MAX_IMAGE_BYTES, ensure_image_under_limit  # noqa: PLC0415

    budget = MAX_IMAGE_BYTES * 3 // 4
    with ensure_image_under_limit(image_path, max_bytes=budget) as prepared:
        raw = prepared.path.read_bytes()
        mime = "image/jpeg" if prepared.path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    b64 = base64.b64encode(raw).decode()
    if len(b64) > MAX_IMAGE_BYTES:
        # The guard could not get under budget (very large lossless source).
        # Send it anyway rather than dropping the figure, but say so.
        logger.warning(
            "%s still encodes to %.1f MB of base64 after compression; the provider may reject it",
            image_path.name,
            len(b64) / 1024 / 1024,
        )
    return mime, b64


def build_messages(prompt: str, mime: str, b64: str, *, system: str | None = None) -> list[dict[str, Any]]:
    """Build the chat payload for one image.

    Without a system prompt the image is placed *before* the text, which is the
    exact ordering docling's ``api_image_request`` used for captions; keeping it
    means switching transports cannot shift caption wording on its own.
    """
    image_part = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
    text_part = {"type": "text", "text": prompt}
    if system is None:
        return [{"role": "user", "content": [image_part, text_part]}]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": [text_part, image_part]},
    ]


def request_once(
    messages: list[dict[str, Any]],
    provider: str,
    model: str,
    settings: Settings,
    api_key: str,
    client: httpx.Client,
    *,
    label: str = "image",
) -> VisionAttempt:
    """Send one vision request and classify the outcome. Never raises on HTTP errors.

    Raises ``typer.Exit`` only for a 404, which means the model slug itself is
    wrong: retrying it on every remaining image would just waste time.
    """
    try:
        resp = client.post(
            get_provider_url(provider, settings),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": _TEMPERATURE,
                "max_tokens": settings.llm_max_tokens,
                "messages": messages,
            },
        )
    except httpx.HTTPError as exc:
        return VisionAttempt(retryable=True, reason=type(exc).__name__)

    if resp.status_code == _HTTP_NOT_FOUND:
        console.print(f"[red]Model not found: {provider}/{model}[/red]")
        raise typer.Exit(1)
    if resp.status_code in RETRYABLE_HTTP_STATUS:
        return VisionAttempt(retryable=True, reason=f"HTTP {resp.status_code}")
    if not resp.is_success:
        logger.warning("Vision API error %d for %s (body: %.200s)", resp.status_code, label, resp.text)
        return VisionAttempt(reason=f"HTTP {resp.status_code}")

    try:
        content: str = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Vision API returned an unusable body for %s: %s", label, exc)
        return VisionAttempt(reason="malformed response")
    if not content.strip():
        # Some providers return an empty candidate under load; worth one retry.
        return VisionAttempt(retryable=True, reason="empty response")
    return VisionAttempt(text=content)


def describe_image(
    image_path: Path,
    prompt: str,
    provider: str,
    model: str,
    settings: Settings,
    api_key: str,
    client: httpx.Client,
    *,
    system: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Describe one image, retrying transient failures. Returns "" when it gave up.

    An empty return is the caller's signal to leave that item undescribed rather
    than to abort the document.
    """
    label = image_path.name
    try:
        mime, b64 = encode_image(image_path)
    except Exception as exc:
        logger.warning("Could not read %s, skipping: %s", label, exc)
        return ""

    messages = build_messages(prompt, mime, b64, system=system)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        outcome = request_once(messages, provider, model, settings, api_key, client, label=label)
        if outcome.text:
            return outcome.text
        if not outcome.retryable:
            return ""
        if attempt == MAX_ATTEMPTS:
            logger.warning("%s still failing after %d attempts, giving up", label, MAX_ATTEMPTS)
            return ""
        wait = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
        logger.warning(
            "%s attempt %d/%d failed (%s), retrying in %.0fs", label, attempt, MAX_ATTEMPTS, outcome.reason, wait
        )
        sleep(wait)
    return ""


def make_client(settings: Settings, workers: int) -> httpx.Client:
    """An httpx client sized for ``workers`` parallel requests."""
    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    return httpx.Client(timeout=settings.llm_timeout, limits=limits)


def map_concurrent(
    items: list[T],
    worker: Callable[[T], R],
    concurrency: int,
    *,
    what: str = "item",
    on_done: Callable[[int, T, R, float], None] | None = None,
) -> list[R]:
    """Run ``worker`` over ``items`` ``concurrency`` at a time, results in input order.

    Logs how much of the total request time was overlapped. Read that number as
    overlap, **not** as speedup against sequential: per-call latency inflates
    under concurrency (measured: figure captions went from 7.8 s sequential to a
    10.5 s median at 8 workers), so the overlap factor is optimistic about the
    real gain. It is still the only figure available from a single run, because
    the provider caches identical requests and a second run would measure the
    cache instead of the change.

    A fatal error (``typer.Exit`` from an unknown model slug) cancels the queued
    work instead of repeating the same failure for every remaining item.
    """
    if not items:
        return []

    workers = max(1, min(concurrency, len(items)))
    results: list[R | None] = [None] * len(items)
    busy_seconds = 0.0
    wall_started = time.monotonic()

    def run(index: int) -> tuple[int, R, float]:
        started = time.monotonic()
        return index, worker(items[index]), time.monotonic() - started

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="vision") as pool:
        futures = [pool.submit(run, i) for i in range(len(items))]
        try:
            for done_count, future in enumerate(as_completed(futures), 1):
                index, value, elapsed = future.result()
                busy_seconds += elapsed
                results[index] = value
                if on_done is not None:
                    on_done(done_count, items[index], value, elapsed)
        except BaseException:
            for future in futures:
                future.cancel()
            raise

    wall = time.monotonic() - wall_started
    logger.info(
        "%d %s(s): %.0fs wall clock for %.0fs of request time (%.1fx overlap on %d workers)",
        len(items),
        what,
        wall,
        busy_seconds,
        busy_seconds / wall if wall > 0 else 1.0,
        workers,
    )
    # Every slot is filled: the loop consumes exactly one future per item, and any
    # failure propagates out above instead of leaving a hole.
    return cast("list[R]", results)
