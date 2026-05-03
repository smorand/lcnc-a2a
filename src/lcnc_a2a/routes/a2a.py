"""A2A protocol surface: POST /agents/<id> and GET .well-known/agent-card.json."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.a2a.card import build_agent_card
from lcnc_a2a.a2a.envelope import (
    A2AEnvelopeError,
    parse_send_streaming_message,
    task_status_update,
)
from lcnc_a2a.a2a.sse import encode_sse_event
from lcnc_a2a.auth.api_key import parse_bearer_header, validate_api_key
from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.deps import get_db
from lcnc_a2a.executors.base import ExecutorContext
from lcnc_a2a.executors.dispatcher import dispatch
from lcnc_a2a.llm.provider import LlmProvider, get_provider
from lcnc_a2a.models.agent import Agent
from lcnc_a2a.services import messages as messages_service
from lcnc_a2a.services import runs as runs_service
from lcnc_a2a.services.cancellation import CancellationRegistry
from lcnc_a2a.services.mcp_discovery import list_servers_for_agent

router = APIRouter()


def _cancellation_registry(request: Request) -> CancellationRegistry:
    registry: CancellationRegistry = request.app.state.cancellation_registry
    return registry


def _llm_provider_for(request: Request, agent: Agent) -> LlmProvider:
    """Resolve the configured LLM provider for ``agent``.

    Tests inject a fake via ``app.state.llm_provider_override``.
    """
    override = getattr(request.app.state, "llm_provider_override", None)
    if override is not None:
        return override  # type: ignore[no-any-return]
    return get_provider(agent.model_provider)


@router.get("/agents/{agent_id}/.well-known/agent-card.json")
async def agent_card(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Return the Agent Card JSON for an agent (FR-011)."""
    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if agent.status != "started":
        return JSONResponse({"error": "agent_stopped"}, status_code=503)
    base_url = str(request.base_url).rstrip("/")
    return JSONResponse(build_agent_card(agent=agent, base_url=base_url))


async def handle_a2a_post(
    *,
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession,
    crypto: CryptoService,
) -> Response:
    """Accept an A2A SendStreamingMessage; respond with SSE."""
    bearer = parse_bearer_header(request.headers.get("authorization"))
    if bearer is None:
        return JSONResponse({"error": "auth_required"}, status_code=401)

    result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if agent is None:
        return JSONResponse({"error": "auth_invalid"}, status_code=403)

    matched = await validate_api_key(db, agent_id=agent_id, plain_key=bearer)
    if matched is None:
        return JSONResponse({"error": "auth_invalid"}, status_code=403)

    if agent.status != "started":
        return JSONResponse({"error": "agent_stopped"}, status_code=503)

    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "envelope_invalid"}, status_code=400)
    try:
        envelope = parse_send_streaming_message(body)
    except A2AEnvelopeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    context_uuid: uuid.UUID | None = None
    if envelope.context_id:
        try:
            context_uuid = uuid.UUID(envelope.context_id)
        except ValueError:
            context_uuid = None

    context = await messages_service.get_or_create_context(db, agent_id=agent_id, context_id=context_uuid)

    run = await runs_service.create_run(
        db,
        agent=agent,
        context_id=context.id,
        a2a_task_id=envelope.task_id or str(uuid.uuid4()),
    )
    await db.commit()

    registry = _cancellation_registry(request)
    cancel_event = registry.register(run.id)

    try:
        provider_key = crypto.decrypt(agent.provider_api_key_enc).decode("utf-8")
    except Exception:
        registry.unregister(run.id)
        return JSONResponse({"error": "provider_key_unreadable"}, status_code=500)

    servers = await list_servers_for_agent(db, agent_id=agent_id)

    provider = _llm_provider_for(request, agent)
    executor = dispatch(mode=agent.mode, db=db, crypto=crypto, provider=provider)
    exec_ctx = ExecutorContext(
        agent=agent,
        run=run,
        context_id=context.id,
        user_text=envelope.text,
        mcp_servers=servers,
        provider_api_key=provider_key,
        cancellation=cancel_event,
    )

    async def _stream() -> AsyncIterator[bytes]:
        try:
            async for chunk in executor.run(exec_ctx):
                yield chunk
        except asyncio.CancelledError:
            yield encode_sse_event(task_status_update("cancelled"))
            raise
        finally:
            registry.unregister(run.id)

    return StreamingResponse(_stream(), media_type="text/event-stream")
