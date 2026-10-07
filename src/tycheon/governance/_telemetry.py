"""Tracing with OpenTelemetry, following the GenAI semantic conventions where they apply.

Keelgate's ``keelgate.telemetry`` (``instrument()``, span helpers) is **not implemented yet** (the
module is empty and the contract lists it as planned), so Tycheon uses the OpenTelemetry API
directly, which Keelgate itself depends on. When Keelgate ships its instrumentation, adopt it
here: this module is the one place that would change.

Spans carry only ids, counts, statuses and token counts: never prompts, document text, or
secrets.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import Iterator

    from opentelemetry.sdk.trace.export import SpanExporter

_PRIMITIVES = (str, bool, int, float)


class OtelTracer:
    """A :class:`~tycheon.agents.protocols.Tracer` backed by an OpenTelemetry tracer."""

    def __init__(self, name: str = "tycheon") -> None:
        self._tracer = trace.get_tracer(name)

    @contextlib.contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[trace.Span]:
        clean = {k: v for k, v in attributes.items() if isinstance(v, _PRIMITIVES)}
        with self._tracer.start_as_current_span(name, attributes=clean) as active:
            yield active


def instrument(
    service_name: str = "tycheon", *, exporter: SpanExporter | None = None
) -> OtelTracer:
    """Install an SDK tracer provider (with ``exporter`` if given) and return a tracer.

    Without an exporter nothing leaves the process. Call once at start-up.
    """
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return OtelTracer(service_name)
