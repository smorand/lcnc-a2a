"""SSE streaming helpers and high-level event emitter for executors."""

from __future__ import annotations

import json
import uuid
from typing import Any

from lcnc_a2a.a2a.envelope import (
    ROLE_AGENT,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_SUBMITTED,
    TASK_STATE_WORKING,
    artifact_update_envelope,
    build_message,
    build_task,
    status_update_envelope,
    task_envelope,
)


def encode_sse_event(payload: dict[str, Any]) -> bytes:
    """Encode a JSON payload as a single SSE ``data:`` event."""
    return f"data: {json.dumps(payload)}\n\n".encode()


class A2AEventEmitter:
    """Build and encode A2A SSE events for one task.

    Bakes ``task_id`` and ``context_id`` so executor call sites stay short.
    All methods return ``bytes`` ready to ``yield`` from a streaming response.
    """

    __slots__ = ("_artifact_id", "_context_id", "_task_id")

    def __init__(self, *, task_id: str, context_id: str) -> None:
        self._task_id = task_id
        self._context_id = context_id
        # One artifact per task is enough for our text-only outputs; reusing
        # the same id with append=True / lastChunk=False allows incremental
        # streaming if we ever need it.
        self._artifact_id = str(uuid.uuid4())

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def context_id(self) -> str:
        return self._context_id

    def initial_task(self, state: str = TASK_STATE_SUBMITTED) -> bytes:
        """First SSE event: the freshly created Task object."""
        return encode_sse_event(
            task_envelope(build_task(task_id=self._task_id, context_id=self._context_id, state=state))
        )

    def working(
        self,
        *,
        message_text: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bytes:
        """Working status update; optional progress message and metadata."""
        message = (
            build_message(
                role=ROLE_AGENT,
                text=message_text,
                context_id=self._context_id,
                task_id=self._task_id,
            )
            if message_text
            else None
        )
        return encode_sse_event(
            status_update_envelope(
                task_id=self._task_id,
                context_id=self._context_id,
                state=TASK_STATE_WORKING,
                message=message,
                final=False,
                metadata=metadata,
            )
        )

    def completed(self) -> bytes:
        return encode_sse_event(
            status_update_envelope(
                task_id=self._task_id,
                context_id=self._context_id,
                state=TASK_STATE_COMPLETED,
                final=True,
            )
        )

    def failed(self, *, reason: str | None = None) -> bytes:
        metadata = {"reason": reason} if reason else None
        return encode_sse_event(
            status_update_envelope(
                task_id=self._task_id,
                context_id=self._context_id,
                state=TASK_STATE_FAILED,
                final=True,
                metadata=metadata,
            )
        )

    def canceled(self) -> bytes:
        return encode_sse_event(
            status_update_envelope(
                task_id=self._task_id,
                context_id=self._context_id,
                state=TASK_STATE_CANCELED,
                final=True,
            )
        )

    def input_required(
        self,
        prompt_text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> bytes:
        """Emit an INPUT_REQUIRED status update: pause the task pending user input.

        The ``prompt_text`` is carried in ``status.message`` as a ROLE_AGENT
        message; A2A clients (e.g. web-a2a) surface it to the user and resume
        by sending a fresh message with the same ``taskId`` + ``contextId``.

        ``final`` is ``False`` because the task is interrupted, not terminal
        (spec section 4.1.3). Optional ``metadata`` is carried verbatim — use
        it to convey machine-readable hints like ``{"kind": "confirm",
        "tool_name": "delete_file", "args": {...}}``.
        """
        message = build_message(
            role=ROLE_AGENT,
            text=prompt_text,
            context_id=self._context_id,
            task_id=self._task_id,
        )
        return encode_sse_event(
            status_update_envelope(
                task_id=self._task_id,
                context_id=self._context_id,
                state=TASK_STATE_INPUT_REQUIRED,
                message=message,
                final=False,
                metadata=metadata,
            )
        )

    def artifact(self, text: str, *, append: bool = False, last_chunk: bool = True) -> bytes:
        """Emit a TaskArtifactUpdateEvent carrying a text part."""
        return encode_sse_event(
            artifact_update_envelope(
                task_id=self._task_id,
                context_id=self._context_id,
                artifact_id=self._artifact_id,
                parts=[{"text": text}],
                append=append,
                last_chunk=last_chunk,
            )
        )


__all__ = ["A2AEventEmitter", "encode_sse_event"]
