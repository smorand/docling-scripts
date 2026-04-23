"""OpenTelemetry tracing configuration."""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import StatusCode

logger = logging.getLogger(__name__)

_tracer: trace.Tracer | None = None


def configure_tracing(service_name: str, *, source_path: str | None = None) -> None:
    """Initialize OpenTelemetry with JSONL file exporter."""
    global _tracer  # noqa: PLW0603

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    try:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter  # noqa: PLC0415

        otel_dir = Path.home() / ".local" / "share" / "doc-convert"
        otel_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path_hash = hashlib.md5((source_path or "none").encode()).hexdigest()[:8]
        log_file = (otel_dir / f"{service_name}-otel-{timestamp}-{path_hash}.log").open("a")
        exporter = ConsoleSpanExporter(out=log_file)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    except Exception:
        logger.debug("OTEL file exporter not available, tracing disabled")

    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(service_name)


@contextmanager
def trace_span(name: str, **attributes: Any) -> Generator[trace.Span, None, None]:
    """Create a traced span with attributes."""
    tracer = _tracer or trace.get_tracer(__name__)
    with tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            span.set_attribute(key, str(value))
        try:
            yield span
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise
