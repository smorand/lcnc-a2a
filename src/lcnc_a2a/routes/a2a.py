"""A2A protocol surface (HTTP+JSON/REST binding).

All routes are mounted under ``/agents/{agent_id}/`` so each agent has its
own A2A base URL with the canonical ``message:send`` / ``message:stream`` /
``tasks/...`` paths and a ``.well-known/a2a/agent-card`` discovery endpoint.

Wire format reference: https://a2a-protocol.org/latest/specification/
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.a2a.card import build_agent_card
from lcnc_a2a.a2a.envelope import (
    ROLE_AGENT,
    ROLE_USER,
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_INPUT_REQUIRED,
    TASK_STATE_SUBMITTED,
    TASK_STATE_WORKING,
    A2AEnvelopeError,
    build_message,
    parse_send_message,
)
from lcnc_a2a.a2a.sse import A2AEventEmitter
from lcnc_a2a.auth.api_key import parse_bearer_header, validate_api_key
from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.deps import get_crypto, get_db
from lcnc_a2a.executors.base import ExecutorContext
from lcnc_a2a.executors.dispatcher import dispatch
from lcnc_a2a.llm.provider import LlmProvider, get_provider
from lcnc_a2a.models.agent import Agent
from lcnc_a2a.models.agent_message import AgentMessage
from lcnc_a2a.models.agent_run import AgentRun
from lcnc_a2a.services import messages as messages_service
from lcnc_a2a.services import runs as runs_service
from lcnc_a2a.services.cancellation import CancellationRegistry
from lcnc_a2a.services.mcp_discovery import list_servers_for_agent

router = APIRouter()


def _cancellation_registry(request: Request) -> CancellationRegistry:
    registry: CancellationRegistry = request.app.state.cancellation_registry
    return registry


class _ProviderKeyEnvMissing(RuntimeError):
    """Raised when ``provider_api_key_env_var`` points at an unset env var."""

    def __init__(self, var: str) -> None:
        super().__init__(f"env var {var!r} is not set")
        self.var = var


def _resolve_provider_key(agent: Agent, crypto: CryptoService) -> str:
    """Return the live provider API key for ``agent``.

    Order:
      - If ``agent.provider_api_key_env_var`` is set, read that env var live
        (env_dynamic source).
      - Else decrypt ``agent.provider_api_key_enc`` (env_snapshot or input).
      - Else (both null/empty, e.g. localhost preset) return the empty string.
    """
    if agent.provider_api_key_env_var:
        value = os.environ.get(agent.provider_api_key_env_var, "")
        if not value:
            raise _ProviderKeyEnvMissing(agent.provider_api_key_env_var)
        return value
    if agent.provider_api_key_enc:
        return crypto.decrypt(agent.provider_api_key_enc).decode("utf-8")
    return ""


def _llm_provider_for(request: Request, agent: Agent) -> LlmProvider:
    """Resolve the configured LLM provider for ``agent`` (test override-friendly)."""
    override = getattr(request.app.state, "llm_provider_override", None)
    if override is not None:
        return override  # type: ignore[no-any-return]
    return get_provider(agent.model_provider)


async def _load_agent_for_a2a(db: AsyncSession, agent_id: uuid.UUID) -> Agent | None:
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    return result.scalar_one_or_none()


def _agent_base_url(request: Request, agent_id: uuid.UUID) -> str:
    """Public base URL for this agent's A2A surface."""
    return f"{str(request.base_url).rstrip('/')}/agents/{agent_id}"


# -------- Discovery -----------------------------------------------------


@router.get("/agents/{agent_id}/.well-known/a2a/agent-card")
async def agent_card(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the Agent Card JSON for an agent (spec section 4.4)."""
    agent = await _load_agent_for_a2a(db, agent_id)
    if agent is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if agent.status != "started":
        return JSONResponse({"error": "agent_stopped"}, status_code=503)
    return JSONResponse(build_agent_card(agent=agent, base_url=_agent_base_url(request, agent_id)))


# -------- Auth helper ---------------------------------------------------


async def _authenticate(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    request: Request,
) -> tuple[Agent | None, Response | None]:
    bearer = parse_bearer_header(request.headers.get("authorization"))
    if bearer is None:
        return None, JSONResponse({"error": "auth_required"}, status_code=401)
    agent = await _load_agent_for_a2a(db, agent_id)
    if agent is None:
        return None, JSONResponse({"error": "auth_invalid"}, status_code=403)
    matched = await validate_api_key(db, agent_id=agent_id, plain_key=bearer)
    if matched is None:
        return None, JSONResponse({"error": "auth_invalid"}, status_code=403)
    if agent.status != "started":
        return None, JSONResponse({"error": "agent_stopped"}, status_code=503)
    return agent, None


# -------- Run setup (shared by send + stream) ---------------------------


async def _prepare_run(
    *,
    db: AsyncSession,
    crypto: CryptoService,
    request: Request,
    agent: Agent,
) -> tuple[ExecutorContext | None, AgentRun | None, asyncio.Event | None, Response | None]:
    """Parse the body, persist the run, and build an ``ExecutorContext``."""
    try:
        body = await request.json()
    except ValueError:
        return None, None, None, JSONResponse({"error": "envelope_invalid"}, status_code=400)
    try:
        envelope = parse_send_message(body)
    except A2AEnvelopeError as exc:
        return None, None, None, JSONResponse({"error": str(exc)}, status_code=400)

    context_uuid: uuid.UUID | None = None
    if envelope.context_id:
        try:
            context_uuid = uuid.UUID(envelope.context_id)
        except ValueError:
            context_uuid = None

    context = await messages_service.get_or_create_context(db, agent_id=agent.id, context_id=context_uuid)
    task_id = envelope.task_id or str(uuid.uuid4())

    # If the request targets an existing paused task, resume it instead of
    # creating a new run (A2A spec section 3.1: continue with same taskId).
    resume_action: dict[str, Any] | None = None
    run: AgentRun | None = None
    if envelope.task_id:
        existing = await runs_service.find_paused_run(
            db,
            agent_id=agent.id,
            a2a_task_id=envelope.task_id,
        )
        if existing is not None:
            resume_action = existing.pending_action if isinstance(existing.pending_action, dict) else {}
            run = existing
    if run is None:
        run = await runs_service.create_run(
            db,
            agent=agent,
            context_id=context.id,
            a2a_task_id=task_id,
        )
    await db.commit()

    registry = _cancellation_registry(request)
    cancel_event = registry.register(run.id)

    try:
        provider_key = _resolve_provider_key(agent, crypto)
    except _ProviderKeyEnvMissing as exc:
        registry.unregister(run.id)
        return (
            None,
            None,
            None,
            JSONResponse({"error": "api_key_env_not_found_at_runtime", "var": exc.var}, status_code=500),
        )
    except Exception:
        registry.unregister(run.id)
        return None, None, None, JSONResponse({"error": "provider_key_unreadable"}, status_code=500)

    servers = await list_servers_for_agent(db, agent_id=agent.id)
    emitter = A2AEventEmitter(task_id=run.a2a_task_id or task_id, context_id=str(context.id))
    ctx = ExecutorContext(
        agent=agent,
        run=run,
        context_id=context.id,
        user_text=envelope.text,
        mcp_servers=servers,
        provider_api_key=provider_key,
        cancellation=cancel_event,
        emitter=emitter,
        resume_action=resume_action,
    )
    return ctx, run, cancel_event, None


# -------- POST /message:stream -----------------------------------------


@router.post("/agents/{agent_id}/message:stream")
async def message_stream(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    crypto: CryptoService = Depends(get_crypto),
) -> Response:
    """A2A SendStreamingMessage: SSE stream of TaskStatus / TaskArtifact updates."""
    agent, err = await _authenticate(db, agent_id=agent_id, request=request)
    if err is not None or agent is None:
        assert err is not None
        return err

    ctx, run, _cancel, err = await _prepare_run(db=db, crypto=crypto, request=request, agent=agent)
    if err is not None or ctx is None or run is None:
        assert err is not None
        return err

    provider = _llm_provider_for(request, agent)
    executor = dispatch(mode=agent.mode, db=db, crypto=crypto, provider=provider)
    emitter = ctx.emitter
    registry = _cancellation_registry(request)

    async def _stream() -> AsyncIterator[bytes]:
        # Initial Task SSE event (spec 3.5.2 StreamResponse one-of: task).
        yield emitter.initial_task(state=TASK_STATE_SUBMITTED)
        try:
            async for chunk in executor.run(ctx):
                yield chunk
        except asyncio.CancelledError:
            yield emitter.canceled()
            raise
        finally:
            registry.unregister(run.id)

    return StreamingResponse(_stream(), media_type="text/event-stream")


# -------- POST /message:send -------------------------------------------


@router.post("/agents/{agent_id}/message:send")
async def message_send(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    crypto: CryptoService = Depends(get_crypto),
) -> Response:
    """A2A SendMessage: synchronous variant. Returns the final ``Task`` JSON."""
    agent, err = await _authenticate(db, agent_id=agent_id, request=request)
    if err is not None or agent is None:
        assert err is not None
        return err

    ctx, run, _cancel, err = await _prepare_run(db=db, crypto=crypto, request=request, agent=agent)
    if err is not None or ctx is None or run is None:
        assert err is not None
        return err

    provider = _llm_provider_for(request, agent)
    executor = dispatch(mode=agent.mode, db=db, crypto=crypto, provider=provider)
    registry = _cancellation_registry(request)

    # Drain the stream and accumulate state.
    final_state = TASK_STATE_WORKING
    final_reason: str | None = None
    artifact_text = ""
    try:
        async for _chunk in executor.run(ctx):
            # The executor mutates the run row in place; we read final state below.
            pass
    finally:
        registry.unregister(run.id)

    await db.refresh(run)
    if run.status == "completed":
        final_state = TASK_STATE_COMPLETED
        artifact_text = run.final_answer or ""
    elif run.status == "cancelled":
        final_state = TASK_STATE_CANCELED
    else:
        final_state = TASK_STATE_FAILED
        final_reason = run.stop_reason

    return JSONResponse(
        _serialize_task(run, agent_id=agent.id, state=final_state, reason=final_reason, artifact_text=artifact_text)
    )


# -------- GET /tasks/{task_id} ------------------------------------------


@router.get("/agents/{agent_id}/tasks/{task_id}")
async def get_task(
    agent_id: uuid.UUID,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the current ``Task`` object for ``task_id``."""
    agent, err = await _authenticate(db, agent_id=agent_id, request=request)
    if err is not None or agent is None:
        assert err is not None
        return err

    run = await _find_run_by_task_id(db, agent_id=agent.id, task_id=task_id)
    if run is None:
        return JSONResponse({"error": "task_not_found"}, status_code=404)

    history = await _build_task_history(db, context_id=run.context_id) if run.context_id else []
    state = _run_status_to_task_state(run.status)
    artifact_text = run.final_answer or ""
    return JSONResponse(
        _serialize_task(
            run,
            agent_id=agent.id,
            state=state,
            reason=run.stop_reason,
            artifact_text=artifact_text,
            history=history,
        )
    )


# -------- POST /tasks/{task_id}:cancel ----------------------------------


@router.post("/agents/{agent_id}/tasks/{task_id}:cancel")
async def cancel_task(
    agent_id: uuid.UUID,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Request cancellation of an in-flight task."""
    agent, err = await _authenticate(db, agent_id=agent_id, request=request)
    if err is not None or agent is None:
        assert err is not None
        return err

    run = await _find_run_by_task_id(db, agent_id=agent.id, task_id=task_id)
    if run is None:
        return JSONResponse({"error": "task_not_found"}, status_code=404)
    if run.status != "running":
        return JSONResponse(
            _serialize_task(
                run,
                agent_id=agent.id,
                state=_run_status_to_task_state(run.status),
                reason=run.stop_reason,
                artifact_text=run.final_answer or "",
            )
        )

    registry = _cancellation_registry(request)
    registry.cancel(run.id)
    return JSONResponse(
        _serialize_task(
            run,
            agent_id=agent.id,
            state=TASK_STATE_CANCELED,
            reason="cancelled",
            artifact_text="",
        )
    )


# -------- GET /tasks (list) ---------------------------------------------


@router.get("/agents/{agent_id}/tasks")
async def list_tasks(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the most recent tasks for an agent (newest first, capped at 100)."""
    agent, err = await _authenticate(db, agent_id=agent_id, request=request)
    if err is not None or agent is None:
        assert err is not None
        return err

    result = await db.execute(
        select(AgentRun).where(AgentRun.agent_id == agent.id).order_by(AgentRun.started_at.desc()).limit(100)
    )
    runs = list(result.scalars().all())
    tasks = [
        _serialize_task(
            run,
            agent_id=agent.id,
            state=_run_status_to_task_state(run.status),
            reason=run.stop_reason,
            artifact_text=run.final_answer or "",
        )
        for run in runs
    ]
    return JSONResponse({"tasks": tasks})


# -------- GET /tasks/{task_id}:subscribe (resubscribe) -----------------


@router.get("/agents/{agent_id}/tasks/{task_id}:subscribe")
async def subscribe_task(
    agent_id: uuid.UUID,
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Resubscribe to a task: emit current snapshot, then a final status update.

    A fully streaming resubscription would require a per-run pub/sub bus, which
    we do not maintain. As a useful approximation, we emit the current Task as
    the initial event followed by a single final status update if the run is
    already terminal.
    """
    agent, err = await _authenticate(db, agent_id=agent_id, request=request)
    if err is not None or agent is None:
        assert err is not None
        return err

    run = await _find_run_by_task_id(db, agent_id=agent.id, task_id=task_id)
    if run is None:
        return JSONResponse({"error": "task_not_found"}, status_code=404)

    state = _run_status_to_task_state(run.status)
    history = await _build_task_history(db, context_id=run.context_id) if run.context_id else []
    artifact_text = run.final_answer or ""
    task_obj = _serialize_task(
        run,
        agent_id=agent.id,
        state=state,
        reason=run.stop_reason,
        artifact_text=artifact_text,
        history=history,
    )

    emitter = A2AEventEmitter(task_id=task_id, context_id=str(run.context_id or ""))

    async def _stream() -> AsyncIterator[bytes]:
        # Initial task snapshot.
        from lcnc_a2a.a2a.envelope import task_envelope
        from lcnc_a2a.a2a.sse import encode_sse_event

        yield encode_sse_event(task_envelope(task_obj))
        # Terminal runs get one final status update; running runs get nothing
        # else (the client has the snapshot and can poll GET /tasks/{id}).
        if state == TASK_STATE_COMPLETED:
            if artifact_text:
                yield emitter.artifact(artifact_text)
            yield emitter.completed()
        elif state == TASK_STATE_FAILED:
            yield emitter.failed(reason=run.stop_reason)
        elif state == TASK_STATE_CANCELED:
            yield emitter.canceled()

    return StreamingResponse(_stream(), media_type="text/event-stream")


# -------- Helpers -------------------------------------------------------


async def _find_run_by_task_id(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    task_id: str,
) -> AgentRun | None:
    result = await db.execute(select(AgentRun).where(AgentRun.agent_id == agent_id, AgentRun.a2a_task_id == task_id))
    return result.scalar_one_or_none()


def _run_status_to_task_state(status: str) -> str:
    return {
        "running": TASK_STATE_WORKING,
        "paused": TASK_STATE_INPUT_REQUIRED,
        "completed": TASK_STATE_COMPLETED,
        "failed": TASK_STATE_FAILED,
        "cancelled": TASK_STATE_CANCELED,
    }.get(status, TASK_STATE_WORKING)


async def _build_task_history(db: AsyncSession, *, context_id: uuid.UUID) -> list[dict[str, Any]]:
    """Convert persisted ``agent_messages`` into A2A Message history entries."""
    result = await db.execute(
        select(AgentMessage).where(AgentMessage.context_id == context_id).order_by(AgentMessage.position)
    )
    out: list[dict[str, Any]] = []
    for row in result.scalars().all():
        if row.role not in ("user", "assistant"):
            continue
        if not row.content:
            continue
        role = ROLE_USER if row.role == "user" else ROLE_AGENT
        out.append(
            build_message(
                role=role,
                text=row.content,
                message_id=str(row.id),
                context_id=str(context_id),
            )
        )
    return out


def _serialize_task(
    run: AgentRun,
    *,
    agent_id: uuid.UUID,
    state: str,
    reason: str | None,
    artifact_text: str,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Materialize an ``agent_runs`` row into a spec-shaped Task object."""
    task_id = run.a2a_task_id or str(run.id)
    context_id = str(run.context_id) if run.context_id else ""
    status: dict[str, Any] = {"state": state}
    if run.completed_at is not None:
        status["timestamp"] = run.completed_at.isoformat().replace("+00:00", "Z")
    elif run.started_at is not None:
        status["timestamp"] = run.started_at.isoformat().replace("+00:00", "Z")
    if reason:
        status["message"] = build_message(
            role=ROLE_AGENT,
            text=reason,
            context_id=context_id or None,
            task_id=task_id,
        )
    out: dict[str, Any] = {
        "id": task_id,
        "contextId": context_id,
        "status": status,
        "metadata": {
            "agentId": str(agent_id),
            "tokensIn": run.tokens_in or 0,
            "tokensOut": run.tokens_out or 0,
            "loops": run.loops or 0,
        },
    }
    if artifact_text:
        out["artifacts"] = [
            {
                "artifactId": f"{task_id}-final",
                "parts": [{"text": artifact_text}],
            }
        ]
    if history:
        out["history"] = history
    return out
