"""Agent CRUD plus 30-day metric aggregation queries."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.models.agent import Agent
from lcnc_a2a.models.agent_run import AgentRun


class AgentNameTakenError(Exception):
    """The (user_id, name) pair already exists."""


@dataclass(frozen=True, slots=True)
class AgentMetrics:
    """Aggregated 30-day metrics for a single agent."""

    requests: int
    tokens_in: int
    tokens_out: int
    avg_duration_ms: float | None
    avg_loops: float | None
    total_time_ms: int
    last_run_at: datetime | None
    total_cost_usd: Decimal | None
    cost_has_unknown: bool


@dataclass(frozen=True, slots=True)
class AgentRow:
    """An agent plus its aggregated metrics for the dashboard view."""

    agent: Agent
    metrics: AgentMetrics


EMPTY_METRICS = AgentMetrics(
    requests=0,
    tokens_in=0,
    tokens_out=0,
    avg_duration_ms=None,
    avg_loops=None,
    total_time_ms=0,
    last_run_at=None,
    total_cost_usd=None,
    cost_has_unknown=False,
)


async def create_agent(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    name: str,
    description: str | None,
    mode: str,
    model_provider: str,
    model_endpoint: str,
    model_id: str,
    provider_api_key: str,
    provider_api_key_env_var: str | None,
    extra_headers: dict[str, str],
    crypto: CryptoService,
    system_prompt: str | None,
    planner_prompt: str | None,
    executor_prompt: str | None,
    max_loops: int,
    max_tokens: int,
    similarity_threshold: float | None,
    max_steps: int | None,
) -> Agent:
    """Persist a new agent for ``user_id`` with the provider API key encrypted at rest."""
    encrypted_key: bytes | None = None
    if provider_api_key:
        encrypted_key = crypto.encrypt(provider_api_key.encode("utf-8"))
    encrypted_headers = (
        crypto.encrypt(json.dumps(extra_headers, sort_keys=True).encode("utf-8")) if extra_headers else None
    )
    agent = Agent(
        user_id=user_id,
        name=name,
        description=description or None,
        mode=mode,
        model_provider=model_provider,
        model_endpoint=model_endpoint,
        model_id=model_id,
        provider_api_key_enc=encrypted_key,
        provider_api_key_env_var=provider_api_key_env_var,
        provider_extra_headers_enc=encrypted_headers,
        system_prompt=system_prompt or None,
        planner_prompt=planner_prompt or None,
        executor_prompt=executor_prompt or None,
        max_loops=max_loops,
        max_tokens=max_tokens,
        similarity_threshold=similarity_threshold,
        max_steps=max_steps,
        status="stopped",
    )
    db.add(agent)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise AgentNameTakenError(name) from exc
    await db.refresh(agent)
    return agent


async def get_agent_for_user(db: AsyncSession, *, agent_id: uuid.UUID, user_id: uuid.UUID) -> Agent | None:
    """Return an agent only if it belongs to ``user_id`` (404 leak protection)."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.user_id == user_id))
    return result.scalar_one_or_none()


async def update_agent(
    db: AsyncSession,
    *,
    agent: Agent,
    name: str,
    description: str | None,
    mode: str,
    model_provider: str,
    model_endpoint: str,
    model_id: str,
    provider_api_key: str,
    provider_api_key_env_var: str | None,
    extra_headers: dict[str, str],
    crypto: CryptoService,
    system_prompt: str | None,
    planner_prompt: str | None,
    executor_prompt: str | None,
    max_loops: int,
    max_tokens: int,
    similarity_threshold: float | None,
    max_steps: int | None,
) -> Agent:
    """Update an existing agent. Empty ``provider_api_key`` (with no env-var marker) keeps the existing key."""
    agent.name = name
    agent.description = description or None
    agent.mode = mode
    agent.model_provider = model_provider
    agent.model_endpoint = model_endpoint
    agent.model_id = model_id
    if provider_api_key_env_var is not None:
        agent.provider_api_key_env_var = provider_api_key_env_var
        agent.provider_api_key_enc = None
    elif provider_api_key:
        agent.provider_api_key_enc = crypto.encrypt(provider_api_key.encode("utf-8"))
        agent.provider_api_key_env_var = None
    # Headers are always overwritten with the form-submitted set: empty
    # dict ⇒ clear column, non-empty ⇒ encrypt fresh JSON.
    agent.provider_extra_headers_enc = (
        crypto.encrypt(json.dumps(extra_headers, sort_keys=True).encode("utf-8")) if extra_headers else None
    )
    agent.system_prompt = system_prompt or None
    agent.planner_prompt = planner_prompt or None
    agent.executor_prompt = executor_prompt or None
    agent.max_loops = max_loops
    agent.max_tokens = max_tokens
    agent.similarity_threshold = similarity_threshold
    agent.max_steps = max_steps
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise AgentNameTakenError(name) from exc
    await db.refresh(agent)
    return agent


async def delete_agent_cascade(db: AsyncSession, *, agent: Agent) -> None:
    """Delete an agent; FK ``ON DELETE CASCADE`` removes all dependent rows."""
    await db.execute(delete(Agent).where(Agent.id == agent.id))


async def set_status(db: AsyncSession, *, agent: Agent, status: str) -> None:
    """Atomically set ``agents.status`` for ``agent`` to ``status``."""
    await db.execute(update(Agent).where(Agent.id == agent.id).values(status=status))


async def list_agents_with_metrics(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    window_days: int,
) -> list[AgentRow]:
    """Return all agents owned by ``user_id`` plus their metrics over the window."""
    cutoff = datetime.now(UTC) - timedelta(days=window_days)

    agent_result = await db.execute(select(Agent).where(Agent.user_id == user_id).order_by(Agent.created_at))
    agents = list(agent_result.scalars().all())
    if not agents:
        return []

    agent_ids = [agent.id for agent in agents]

    metrics_result = await db.execute(
        select(
            AgentRun.agent_id,
            func.count().label("requests"),
            func.coalesce(func.sum(AgentRun.tokens_in), 0).label("tokens_in"),
            func.coalesce(func.sum(AgentRun.tokens_out), 0).label("tokens_out"),
            func.avg(AgentRun.duration_ms).label("avg_duration_ms"),
            func.avg(AgentRun.loops).label("avg_loops"),
            func.coalesce(func.sum(AgentRun.duration_ms), 0).label("total_time_ms"),
            func.max(AgentRun.started_at).label("last_run_at"),
            func.sum(AgentRun.cost_usd).label("total_cost_usd"),
            func.sum(case((AgentRun.cost_usd.is_(None), 1), else_=0)).label("null_cost_count"),
        )
        .where(AgentRun.agent_id.in_(agent_ids), AgentRun.started_at >= cutoff)
        .group_by(AgentRun.agent_id)
    )

    metrics_by_agent: dict[uuid.UUID, AgentMetrics] = {}
    for row in metrics_result.all():
        null_count = int(row.null_cost_count or 0)
        cost_has_unknown = null_count > 0
        total_cost = None if cost_has_unknown else (row.total_cost_usd or Decimal("0"))
        metrics_by_agent[row.agent_id] = AgentMetrics(
            requests=int(row.requests),
            tokens_in=int(row.tokens_in),
            tokens_out=int(row.tokens_out),
            avg_duration_ms=float(row.avg_duration_ms) if row.avg_duration_ms is not None else None,
            avg_loops=float(row.avg_loops) if row.avg_loops is not None else None,
            total_time_ms=int(row.total_time_ms),
            last_run_at=row.last_run_at,
            total_cost_usd=total_cost,
            cost_has_unknown=cost_has_unknown,
        )

    return [AgentRow(agent=agent, metrics=metrics_by_agent.get(agent.id, EMPTY_METRICS)) for agent in agents]
