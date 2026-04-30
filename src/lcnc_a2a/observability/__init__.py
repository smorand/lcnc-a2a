"""OpenTelemetry tracing scaffolding."""

from __future__ import annotations

from lcnc_a2a.observability.jsonl_exporter import JSONLSpanExporter
from lcnc_a2a.observability.otel import configure_tracing, get_tracer

__all__ = ["JSONLSpanExporter", "configure_tracing", "get_tracer"]
