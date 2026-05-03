"""A2A envelope shapes (subset implemented for US-005)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class A2AEnvelopeError(ValueError):
    """Raised when an A2A request envelope is malformed."""


@dataclass(frozen=True, slots=True)
class SendStreamingMessage:
    """Parsed A2A SendStreamingMessage request."""

    text: str
    context_id: str | None
    task_id: str | None


def parse_send_streaming_message(payload: dict[str, Any]) -> SendStreamingMessage:
    """Parse the JSON body of POST /agents/<id>."""
    if not isinstance(payload, dict):
        raise A2AEnvelopeError("envelope_invalid")
    message = payload.get("message")
    if not isinstance(message, dict):
        raise A2AEnvelopeError("message_required")
    parts = message.get("parts")
    if not isinstance(parts, list) or not parts:
        raise A2AEnvelopeError("parts_required")
    text_chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("kind") == "text":
            value = part.get("text", "")
            if isinstance(value, str):
                text_chunks.append(value)
    if not text_chunks:
        raise A2AEnvelopeError("text_part_required")
    context_id = payload.get("contextId") or message.get("contextId")
    task_id = payload.get("taskId") or message.get("taskId")
    return SendStreamingMessage(
        text="".join(text_chunks),
        context_id=context_id if isinstance(context_id, str) else None,
        task_id=task_id if isinstance(task_id, str) else None,
    )


def task_status_update(state: str, *, reason: str | None = None) -> dict[str, Any]:
    """Build a TaskStatusUpdate envelope."""
    payload: dict[str, Any] = {"event": "TaskStatusUpdate", "state": state}
    if reason is not None:
        payload["reason"] = reason
    return payload


def task_artifact_update(text: str) -> dict[str, Any]:
    """Build a TaskArtifactUpdate envelope carrying a text part."""
    return {
        "event": "TaskArtifactUpdate",
        "artifact": {"parts": [{"kind": "text", "text": text}]},
    }
