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
                    payload = {
                        "name": span.name,
                        "trace_id": format(span.context.trace_id, "032x") if span.context else None,
                        "span_id": format(span.context.span_id, "016x") if span.context else None,
                        "start_time": span.start_time,
                        "end_time": span.end_time,
                        "attributes": _redact(dict(span.attributes or {})),
                    }
                    fh.write(json.dumps(payload) + "\n")
        except OSError:
            return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return
