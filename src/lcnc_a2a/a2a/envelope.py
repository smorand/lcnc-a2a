"""A2A envelope shapes (HTTP+JSON/REST binding, spec v1).

Wire format reference:
  https://a2a-protocol.org/latest/specification/

The Part one-of in the spec is discriminated by *which field is present*
(``text`` / ``raw`` / ``url`` / ``data``), not by an explicit ``kind`` tag.
TaskState values are the protobuf-style strings (``TASK_STATE_WORKING`` etc.)
and Role values are ``ROLE_USER`` / ``ROLE_AGENT``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# TaskState enum values (spec section 4.1.3).
TASK_STATE_SUBMITTED = "TASK_STATE_SUBMITTED"
TASK_STATE_WORKING = "TASK_STATE_WORKING"
TASK_STATE_COMPLETED = "TASK_STATE_COMPLETED"
TASK_STATE_FAILED = "TASK_STATE_FAILED"
TASK_STATE_CANCELED = "TASK_STATE_CANCELED"
TASK_STATE_REJECTED = "TASK_STATE_REJECTED"
TASK_STATE_INPUT_REQUIRED = "TASK_STATE_INPUT_REQUIRED"
TASK_STATE_AUTH_REQUIRED = "TASK_STATE_AUTH_REQUIRED"

ROLE_USER = "ROLE_USER"
ROLE_AGENT = "ROLE_AGENT"


class A2AEnvelopeError(ValueError):
    """Raised when an A2A request envelope is malformed."""


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """Parsed A2A SendMessage / SendStreamingMessage request."""

    text: str
    message_id: str
    context_id: str | None
    task_id: str | None


def parse_send_message(payload: dict[str, Any]) -> IncomingMessage:
    """Parse an A2A SendMessage or SendStreamingMessage request body.

    Accepts a shape like::

        {"message": {"messageId": "...", "role": "ROLE_USER",
                     "parts": [{"text": "hello"}], "contextId": "..."}}

    The Part one-of is discriminated by which field is present; ``text`` parts
    are concatenated and returned as a single string. Other Part kinds (raw,
    url, data) are accepted but ignored for now (FR-future).
    """
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
        text_value = part.get("text")
        if isinstance(text_value, str):
            text_chunks.append(text_value)
    if not text_chunks:
        raise A2AEnvelopeError("text_part_required")

    raw_message_id = message.get("messageId")
    message_id = raw_message_id if isinstance(raw_message_id, str) and raw_message_id else str(uuid.uuid4())

    context_id_raw = message.get("contextId") or payload.get("contextId")
    task_id_raw = message.get("taskId") or payload.get("taskId")

    return IncomingMessage(
        text="".join(text_chunks),
        message_id=message_id,
        context_id=context_id_raw if isinstance(context_id_raw, str) else None,
        task_id=task_id_raw if isinstance(task_id_raw, str) else None,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_text_part(text: str) -> dict[str, Any]:
    """Return a Part one-of with the ``text`` discriminator set."""
    return {"text": text}


def build_message(
    *,
    role: str,
    text: str,
    message_id: str | None = None,
    context_id: str | None = None,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Message object (spec section 4.1.4)."""
    out: dict[str, Any] = {
        "messageId": message_id or str(uuid.uuid4()),
        "role": role,
        "parts": [build_text_part(text)],
    }
    if context_id is not None:
        out["contextId"] = context_id
    if task_id is not None:
        out["taskId"] = task_id
    if metadata:
        out["metadata"] = metadata
    return out


def build_task(
    *,
    task_id: str,
    context_id: str,
    state: str = TASK_STATE_SUBMITTED,
    artifacts: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Task object (spec section 4.1.1)."""
    out: dict[str, Any] = {
        "id": task_id,
        "contextId": context_id,
        "status": {"state": state, "timestamp": _now_iso()},
    }
    if artifacts:
        out["artifacts"] = artifacts
    if history:
        out["history"] = history
    if metadata:
        out["metadata"] = metadata
    return out


def task_envelope(task: dict[str, Any]) -> dict[str, Any]:
    """Wrap a Task in a StreamResponse one-of (initial task SSE event)."""
    return {"task": task}


def status_update_envelope(
    *,
    task_id: str,
    context_id: str,
    state: str,
    message: dict[str, Any] | None = None,
    final: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a TaskStatusUpdateEvent (spec section 4.2.1) wrapped for SSE."""
    status: dict[str, Any] = {"state": state, "timestamp": _now_iso()}
    if message is not None:
        status["message"] = message
    update: dict[str, Any] = {
        "taskId": task_id,
        "contextId": context_id,
        "status": status,
        "final": final,
    }
    if metadata:
        update["metadata"] = metadata
    return {"statusUpdate": update}


def artifact_update_envelope(
    *,
    task_id: str,
    context_id: str,
    artifact_id: str,
    parts: list[dict[str, Any]],
    name: str | None = None,
    append: bool = False,
    last_chunk: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a TaskArtifactUpdateEvent (spec section 4.2.2) wrapped for SSE."""
    artifact: dict[str, Any] = {"artifactId": artifact_id, "parts": parts}
    if name is not None:
        artifact["name"] = name
    update: dict[str, Any] = {
        "taskId": task_id,
        "contextId": context_id,
        "artifact": artifact,
        "append": append,
        "lastChunk": last_chunk,
    }
    if metadata:
        update["metadata"] = metadata
    return {"artifactUpdate": update}


__all__ = [
    "ROLE_AGENT",
    "ROLE_USER",
    "TASK_STATE_AUTH_REQUIRED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_INPUT_REQUIRED",
    "TASK_STATE_REJECTED",
    "TASK_STATE_SUBMITTED",
    "TASK_STATE_WORKING",
    "A2AEnvelopeError",
    "IncomingMessage",
    "artifact_update_envelope",
    "build_message",
    "build_task",
    "build_text_part",
    "parse_send_message",
    "status_update_envelope",
    "task_envelope",
]
