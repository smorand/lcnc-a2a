"""In-process cancellation registry for in-flight A2A runs."""

from __future__ import annotations

import asyncio
import uuid


class CancellationRegistry:
    """Track ``run_id → asyncio.Event`` to signal in-flight cancellation."""

    __slots__ = ("_events",)

    def __init__(self) -> None:
        self._events: dict[uuid.UUID, asyncio.Event] = {}

    def register(self, run_id: uuid.UUID) -> asyncio.Event:
        event = asyncio.Event()
        self._events[run_id] = event
        return event

    def unregister(self, run_id: uuid.UUID) -> None:
        self._events.pop(run_id, None)

    def cancel_all_for_agent(self, run_ids: list[uuid.UUID]) -> None:
        for run_id in run_ids:
            event = self._events.get(run_id)
            if event is not None:
                event.set()

    def is_cancelled(self, run_id: uuid.UUID) -> bool:
        event = self._events.get(run_id)
        return event is not None and event.is_set()
