"""Helpers for the US-005 A2A acceptance tests."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
import respx
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

LLM_ENDPOINT = "https://openrouter.example.com/api/v1"
LLM_CHAT_URL = f"{LLM_ENDPOINT}/chat/completions"


@dataclass
class StubLlm:
    """Programmable LLM mock backed by ``respx``."""

    responses: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    on_call: Callable[[int, dict[str, Any]], Awaitable[None]] | None = None
    repeat_last: bool = False

    def add_text(
        self, content: str, *, prompt_tokens: int = 5, completion_tokens: int = 5, cost: float | None = 0.0001
    ) -> None:
        usage: dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if cost is not None:
            usage["cost"] = cost
        self.responses.append(
            {
                "id": f"resp-{len(self.responses)}",
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": usage,
            }
        )

    def add_tool_call(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        prompt_tokens: int = 5,
        completion_tokens: int = 5,
        cost: float | None = 0.0001,
        call_id: str | None = None,
    ) -> None:
        usage: dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if cost is not None:
            usage["cost"] = cost
        tool_call_id = call_id or f"call-{len(self.responses)}"
        self.responses.append(
            {
                "id": f"resp-{len(self.responses)}",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": tool_call_id,
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": usage,
            }
        )

    def add_status(self, *, status: int, body: str = "boom") -> None:
        self.responses.append({"_status": status, "_body": body})

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body_bytes = await request.aread() if not request.content else request.content
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        index = len(self.calls)
        self.calls.append(payload)
        if self.on_call is not None:
            await self.on_call(index, payload)
        if index < len(self.responses):
            response = self.responses[index]
        elif self.repeat_last and self.responses:
            response = self.responses[-1]
        else:
            response = {
                "id": "resp-default",
                "choices": [{"message": {"role": "assistant", "content": "default"}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0},
            }
        if "_status" in response:
            return httpx.Response(int(response["_status"]), text=str(response.get("_body", "")))
        return httpx.Response(200, json=response)


def install_llm_mock(mock: respx.Router, stub: StubLlm) -> None:
    mock.post(LLM_CHAT_URL).mock(side_effect=stub)


async def seed_started_agent(
    db_engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    name: str = "agent-A",
    mode: str = "simple",
    description: str = "A test agent.",
    model_endpoint: str = LLM_ENDPOINT,
    model_provider: str = "openrouter",
) -> tuple[uuid.UUID, str]:
    """Insert a started agent + one API key; return ``(agent_id, plain_key)``."""
    fernet = Fernet(os.environ["LCNC_A2A_ENCRYPTION_KEY"].encode())
    enc_key = fernet.encrypt(b"sk-fake")
    agent_id = uuid.uuid4()
    plain_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plain_key.encode()).digest()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents (id, user_id, name, description, mode, model_provider, "
                "model_endpoint, model_id, provider_api_key_enc, max_loops, max_tokens, "
                "system_prompt, status) VALUES (:id, :user_id, :name, :description, :mode, "
                ":model_provider, :model_endpoint, 'mock-model', :enc, 10, 8000, 'You are helpful.', "
                "'started')"
            ),
            {
                "id": agent_id,
                "user_id": user_id,
                "name": name,
                "description": description,
                "mode": mode,
                "model_provider": model_provider,
                "model_endpoint": model_endpoint,
                "enc": enc_key,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO agent_api_keys (id, agent_id, label, key_hash, key_last4) "
                "VALUES (:id, :agent_id, 'default', :key_hash, :key_last4)"
            ),
            {
                "id": uuid.uuid4(),
                "agent_id": agent_id,
                "key_hash": key_hash,
                "key_last4": plain_key[-4:],
            },
        )
    return agent_id, plain_key


async def seed_mcp_server_with_cache(
    db_engine: AsyncEngine,
    *,
    agent_id: uuid.UUID,
    command: str,
    tools: list[dict[str, Any]],
) -> uuid.UUID:
    """Insert an MCP stdio server row with a populated ``tools_cache``."""
    server_id = uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, command, "
                "tools_cache, discovered_at) VALUES (:id, :agent_id, 'stdio', :command, "
                ":cache, :now)"
            ),
            {
                "id": server_id,
                "agent_id": agent_id,
                "command": command,
                "cache": json.dumps({"tools": tools}),
                "now": datetime.now(UTC),
            },
        )
    return server_id


def make_a2a_envelope(
    text: str,
    *,
    context_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Build a minimal SendStreamingMessage envelope (spec wire format)."""
    message: dict[str, Any] = {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if context_id is not None:
        message["contextId"] = context_id
    if task_id is not None:
        message["taskId"] = task_id
    return {"message": message}


async def collect_sse(stream: httpx.Response) -> list[dict[str, Any]]:
    """Collect every parsed JSON event from an SSE stream."""
    events: list[dict[str, Any]] = []
    async for chunk in stream.aiter_lines():
        line = chunk.strip()
        if not line:
            continue
        if line.startswith("data:"):
            data = line[5:].strip()
            if data:
                events.append(json.loads(data))
    return events


async def post_a2a(
    client: httpx.AsyncClient,
    *,
    agent_id: uuid.UUID,
    plain_key: str | None,
    body: dict[str, Any],
) -> tuple[int, list[dict[str, Any]] | str, dict[str, str]]:
    """POST to ``/agents/<id>/message:stream`` and return ``(status, body_or_events, headers)``."""
    headers: dict[str, str] = {}
    if plain_key is not None:
        headers["Authorization"] = f"Bearer {plain_key}"
    async with client.stream(
        "POST",
        f"/agents/{agent_id}/message:stream",
        json=body,
        headers=headers,
    ) as response:
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            events = await collect_sse(response)
            return response.status_code, events, dict(response.headers)
        text_body = await response.aread()
        return response.status_code, text_body.decode("utf-8"), dict(response.headers)


async def fetch_run_row(db_engine: AsyncEngine, run_id: uuid.UUID) -> dict[str, Any]:
    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT id, status, stop_reason, loops, tokens_in, tokens_out, "
                    "cost_usd, final_answer, config_snapshot FROM agent_runs WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).one_or_none()
    if row is None:
        return {}
    return dict(row._mapping)


async def fetch_runs_for_agent(db_engine: AsyncEngine, agent_id: uuid.UUID) -> list[dict[str, Any]]:
    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, status, stop_reason, loops, tokens_in, tokens_out, "
                    "cost_usd, final_answer, config_snapshot, started_at FROM agent_runs "
                    "WHERE agent_id = :a ORDER BY started_at"
                ),
                {"a": agent_id},
            )
        ).all()
    return [dict(r._mapping) for r in rows]


async def fetch_messages(db_engine: AsyncEngine, context_id: uuid.UUID) -> list[dict[str, Any]]:
    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT role, content, tool_call_json, tool_call_id, position FROM "
                    "agent_messages WHERE context_id = :c ORDER BY position"
                ),
                {"c": context_id},
            )
        ).all()
    return [dict(r._mapping) for r in rows]


async def fetch_steps(db_engine: AsyncEngine, run_id: uuid.UUID) -> list[dict[str, Any]]:
    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT seq, role, content, tool_name, tool_args_json, "
                    "tool_result_json FROM agent_run_steps WHERE run_id = :r ORDER BY seq"
                ),
                {"r": run_id},
            )
        ).all()
    return [dict(r._mapping) for r in rows]


async def stream_lines_for_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
) -> AsyncIterator[str]:
    async with client.stream("POST", url, json=body, headers=headers) as response:
        async for line in response.aiter_lines():
            yield line


# ---- SSE event helpers (spec wire format) ------------------------------


def is_status_event(event: dict[str, Any]) -> bool:
    """True for ``TaskStatusUpdateEvent`` SSE payloads."""
    return isinstance(event, dict) and "statusUpdate" in event


def is_artifact_event(event: dict[str, Any]) -> bool:
    """True for ``TaskArtifactUpdateEvent`` SSE payloads."""
    return isinstance(event, dict) and "artifactUpdate" in event


def is_task_event(event: dict[str, Any]) -> bool:
    """True for the initial ``Task`` SSE payload."""
    return isinstance(event, dict) and "task" in event


def event_state(event: dict[str, Any]) -> str | None:
    """Extract ``status.state`` from a TaskStatusUpdateEvent."""
    update = event.get("statusUpdate") if isinstance(event, dict) else None
    if not isinstance(update, dict):
        return None
    status = update.get("status")
    if not isinstance(status, dict):
        return None
    state = status.get("state")
    return state if isinstance(state, str) else None


def event_reason(event: dict[str, Any]) -> str | None:
    """Extract the ``reason`` from a TaskStatusUpdateEvent's metadata."""
    update = event.get("statusUpdate") if isinstance(event, dict) else None
    if not isinstance(update, dict):
        return None
    metadata = update.get("metadata")
    if not isinstance(metadata, dict):
        return None
    reason = metadata.get("reason")
    return reason if isinstance(reason, str) else None


def event_phase(event: dict[str, Any]) -> str | None:
    """Extract the ``phase`` metadata from a TaskStatusUpdateEvent."""
    update = event.get("statusUpdate") if isinstance(event, dict) else None
    if not isinstance(update, dict):
        return None
    metadata = update.get("metadata")
    if not isinstance(metadata, dict):
        return None
    phase = metadata.get("phase")
    return phase if isinstance(phase, str) else None


def artifact_text(event: dict[str, Any]) -> str:
    """Concatenate ``text`` parts from a TaskArtifactUpdateEvent."""
    update = event.get("artifactUpdate") if isinstance(event, dict) else None
    if not isinstance(update, dict):
        return ""
    artifact = update.get("artifact")
    if not isinstance(artifact, dict):
        return ""
    parts = artifact.get("parts")
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part["text"])
    return "".join(out)


__all__ = [
    "LLM_CHAT_URL",
    "LLM_ENDPOINT",
    "StubLlm",
    "artifact_text",
    "collect_sse",
    "event_phase",
    "event_reason",
    "event_state",
    "fetch_messages",
    "fetch_run_row",
    "fetch_runs_for_agent",
    "fetch_steps",
    "install_llm_mock",
    "is_artifact_event",
    "is_status_event",
    "is_task_event",
    "make_a2a_envelope",
    "post_a2a",
    "seed_mcp_server_with_cache",
    "seed_started_agent",
]
