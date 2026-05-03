"""Read-only query helpers for the runs history & per-run trace UI (US-008)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.models.agent_run import AgentRun
from lcnc_a2a.models.agent_run_step import AgentRunStep

SUMMARY_LIMIT = 80
PAYLOAD_TRUNCATE_THRESHOLD = 4096
ELLIPSIS = "…"


def summarize_final_answer(final_answer: str | None) -> tuple[str, bool]:
    """Return ``(text, truncated)`` for the runs-list summary cell."""
    if not final_answer:
        return "", False
    if len(final_answer) <= SUMMARY_LIMIT:
        return final_answer, False
    return final_answer[:SUMMARY_LIMIT], True


def serialize_payload(payload: Any) -> str:
    """Render a JSON payload as compact text (or empty string if missing)."""
    if payload is None:
        return ""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def truncate_payload(payload_text: str) -> tuple[str, bool]:
    """Truncate to fit within 4 KB once an ellipsis indicator is appended.

    Returns ``(displayed_text, was_truncated)``. When truncated, the displayed
    text already includes the trailing ellipsis and is exactly
    ``PAYLOAD_TRUNCATE_THRESHOLD`` characters long.
    """
    if len(payload_text) <= PAYLOAD_TRUNCATE_THRESHOLD:
        return payload_text, False
    keep = PAYLOAD_TRUNCATE_THRESHOLD - len(ELLIPSIS)
    return payload_text[:keep] + ELLIPSIS, True


async def list_recent_runs(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    limit: int = 100,
) -> list[AgentRun]:
    """Return the ``limit`` most recent runs for ``agent_id`` (started_at DESC)."""
    result = await db.execute(
        select(AgentRun).where(AgentRun.agent_id == agent_id).order_by(AgentRun.started_at.desc()).limit(limit),
    )
    return list(result.scalars().all())


async def get_run_for_agent(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    run_id: uuid.UUID,
) -> AgentRun | None:
    """Return a run only if it belongs to ``agent_id`` (404 leak protection)."""
    result = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id, AgentRun.agent_id == agent_id),
    )
    return result.scalar_one_or_none()


async def list_steps(db: AsyncSession, *, run_id: uuid.UUID) -> list[AgentRunStep]:
    """Return all steps for ``run_id`` ordered by seq ascending."""
    result = await db.execute(
        select(AgentRunStep).where(AgentRunStep.run_id == run_id).order_by(AgentRunStep.seq),
    )
    return list(result.scalars().all())


async def get_step_for_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    step_id: uuid.UUID,
) -> AgentRunStep | None:
    """Return a step only if it belongs to ``run_id``."""
    result = await db.execute(
        select(AgentRunStep).where(AgentRunStep.id == step_id, AgentRunStep.run_id == run_id),
    )
    return result.scalar_one_or_none()
