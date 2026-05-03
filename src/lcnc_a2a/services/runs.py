"""AgentRun lifecycle helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.models.agent import Agent
from lcnc_a2a.models.agent_run import AgentRun
from lcnc_a2a.models.agent_run_step import AgentRunStep


def snapshot_agent_config(agent: Agent) -> dict[str, Any]:
    """Capture the subset of agent config that affects this run."""
    return {
        "name": agent.name,
        "mode": agent.mode,
        "model_provider": agent.model_provider,
        "model_endpoint": agent.model_endpoint,
        "model_id": agent.model_id,
        "system_prompt": agent.system_prompt,
        "planner_prompt": agent.planner_prompt,
        "executor_prompt": agent.executor_prompt,
        "max_loops": agent.max_loops,
        "max_tokens": agent.max_tokens,
        "max_steps": agent.max_steps,
        "similarity_threshold": agent.similarity_threshold,
    }


async def create_run(
    db: AsyncSession,
    *,
    agent: Agent,
    context_id: uuid.UUID | None,
    a2a_task_id: str,
) -> AgentRun:
    """Insert a fresh ``running`` row and return it."""
    run = AgentRun(
        agent_id=agent.id,
        context_id=context_id,
        a2a_task_id=a2a_task_id,
        status="running",
        started_at=datetime.now(UTC),
        loops=0,
        tokens_in=0,
        tokens_out=0,
        config_snapshot=snapshot_agent_config(agent),
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def append_run_step(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    seq: int,
    role: str,
    content: str | None = None,
    tool_name: str | None = None,
    tool_args_json: Any | None = None,
    tool_result_json: Any | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_ms: int | None = None,
) -> AgentRunStep:
    step = AgentRunStep(
        run_id=run_id,
        seq=seq,
        role=role,
        content=content,
        tool_name=tool_name,
        tool_args_json=tool_args_json,
        tool_result_json=tool_result_json,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        duration_ms=duration_ms,
    )
    db.add(step)
    await db.flush()
    return step


async def finalize_run(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    status: str,
    stop_reason: str | None,
    final_answer: str | None,
    tokens_in: int,
    tokens_out: int,
    cost_usd: Decimal | None,
    loops: int,
) -> None:
    """Update the run row to its terminal state. No-ops if the row is gone."""
    result = await db.execute(select(AgentRun).where(AgentRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        return
    completed = datetime.now(UTC)
    run.status = status
    run.stop_reason = stop_reason
    run.final_answer = final_answer
    run.tokens_in = tokens_in
    run.tokens_out = tokens_out
    run.cost_usd = cost_usd
    run.loops = loops
    run.completed_at = completed
    started = run.started_at
    if started is not None:
        run.duration_ms = int((completed - started).total_seconds() * 1000)
    await db.flush()


async def list_running_run_ids_for_agent(db: AsyncSession, *, agent_id: uuid.UUID) -> list[uuid.UUID]:
    """Return the IDs of all ``running`` runs for an agent."""
    result = await db.execute(select(AgentRun.id).where(AgentRun.agent_id == agent_id, AgentRun.status == "running"))
    return [row[0] for row in result.all()]
