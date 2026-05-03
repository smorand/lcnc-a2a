"""JSONL span exporter with field redaction for sensitive data."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

REDACTED = "<redacted>"
REDACT_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "set-cookie",
    "cookie",
    "llm.prompt",
    "llm.response",
}

LLM_CHAT_ALLOWED_KEYS = {
    "model",
    "provider",
    "tokens.prompt",
    "tokens.completion",
    "cost.usd",
    "duration.ms",
    "request_id",
}

LLM_EMBED_ALLOWED_KEYS = LLM_CHAT_ALLOWED_KEYS


def _filter_allowed(attributes: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    """Strict allow-list filter (FR-024)."""
    return {k: v for k, v in attributes.items() if k in allowed}


def _redact(attributes: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in attributes.items():
        if key.lower() in REDACT_KEYS:
            redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted


class JSONLSpanExporter(SpanExporter):
    """Append spans as JSON lines to a file, redacting sensitive attributes."""

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            with self._path.open("a", encoding="utf-8") as fh:
                for span in spans:
                    raw_attrs = dict(span.attributes or {})
                    if span.name.startswith("llm.chat"):
                        attrs = _filter_allowed(raw_attrs, LLM_CHAT_ALLOWED_KEYS)
                    elif span.name.startswith("llm.embed"):
                        attrs = _filter_allowed(raw_attrs, LLM_EMBED_ALLOWED_KEYS)
                    else:
                        attrs = _redact(raw_attrs)
                    payload = {
                        "name": span.name,
                        "trace_id": format(span.context.trace_id, "032x") if span.context else None,
                        "span_id": format(span.context.span_id, "016x") if span.context else None,
                        "start_time": span.start_time,
                        "end_time": span.end_time,
                        "attributes": attrs,
                    }
                    fh.write(json.dumps(payload) + "\n")
        except OSError:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return
