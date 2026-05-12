"""Per-context conversation memory (FR-021)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.models.agent_context import AgentContext
from lcnc_a2a.models.agent_message import AgentMessage

SOFT_CAP = 50
HARD_CAP = 1000


class ContextFullError(Exception):
    """Raised when a context already holds ``HARD_CAP`` messages."""


async def get_or_create_context(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    context_id: uuid.UUID | None,
) -> AgentContext:
    """Load an existing context or create a new one for ``agent_id``."""
    if context_id is not None:
        result = await db.execute(
            select(AgentContext).where(
                AgentContext.id == context_id,
                AgentContext.agent_id == agent_id,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
    # Honor the client-supplied contextId so subsequent turns find the same
    # row. Without this, A2A's stable conversation identifier is silently
    # replaced by a server-generated UUID and history is lost across turns.
    context = AgentContext(agent_id=agent_id)
    if context_id is not None:
        context.id = context_id
    db.add(context)
    await db.flush()
    await db.refresh(context)
    return context


async def count_messages(db: AsyncSession, *, context_id: uuid.UUID) -> int:
    result = await db.execute(
        select(func.count()).select_from(AgentMessage).where(AgentMessage.context_id == context_id)
    )
    return int(result.scalar_one())


async def list_messages(
    db: AsyncSession,
    *,
    context_id: uuid.UUID,
) -> list[AgentMessage]:
    """All persisted messages for ``context_id`` in ``position`` order."""
    result = await db.execute(
        select(AgentMessage).where(AgentMessage.context_id == context_id).order_by(AgentMessage.position)
    )
    return list(result.scalars().all())


async def append_message(
    db: AsyncSession,
    *,
    context_id: uuid.UUID,
    role: str,
    content: str,
    tool_call_json: Any | None = None,
    tool_call_id: str | None = None,
) -> AgentMessage:
    """Append one message; raises :class:`ContextFullError` at the hard cap."""
    current = await count_messages(db, context_id=context_id)
    if current >= HARD_CAP:
        raise ContextFullError("context_full")
    message = AgentMessage(
        context_id=context_id,
        role=role,
        content=content,
        tool_call_json=tool_call_json,
        tool_call_id=tool_call_id,
        position=current,
    )
    db.add(message)
    await db.execute(
        update(AgentContext)
        .where(AgentContext.id == context_id)
        .values(message_count=current + 1, last_used_at=datetime.now(UTC))
    )
    await db.flush()
    await db.refresh(message)
    return message


def build_llm_payload(
    persisted: list[AgentMessage],
    *,
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    """Apply the soft cap and convert persisted rows to OpenAI messages format."""
    payload: list[dict[str, Any]] = []
    if system_prompt:
        payload.append({"role": "system", "content": system_prompt})

    non_system = [m for m in persisted if m.role != "system"]
    keep_count = SOFT_CAP - len(payload)
    if len(non_system) > keep_count:
        non_system = non_system[-keep_count:]

    for message in non_system:
        payload.append(_serialize_message(message))
    return payload


def _serialize_message(message: AgentMessage) -> dict[str, Any]:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id or "",
            "content": message.content,
        }
    if message.role == "assistant" and message.tool_call_json:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": message.tool_call_json,
        }
    return {"role": message.role, "content": message.content}
