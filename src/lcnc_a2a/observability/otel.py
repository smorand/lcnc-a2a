"""OpenTelemetry tracer setup."""

from __future__ import annotations

from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from lcnc_a2a.observability.jsonl_exporter import JSONLSpanExporter

_TRACER_NAME = "lcnc-a2a"
_provider_initialized = False


def configure_tracing(trace_file: Path) -> None:
    """Configure the global TracerProvider with a JSONL exporter."""
    global _provider_initialized
    if _provider_initialized:
        return
    resource = Resource.create({SERVICE_NAME: _TRACER_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(JSONLSpanExporter(trace_file)))
    trace.set_tracer_provider(provider)
    _provider_initialized = True


def get_tracer() -> trace.Tracer:
    """Return the application tracer."""
    return trace.get_tracer(_TRACER_NAME)
