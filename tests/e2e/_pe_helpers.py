"""Helpers for the US-007 Plan & Execute acceptance tests."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import LLM_ENDPOINT, StubLlm

FAKE_MCP_PE_CMD = "python -m tests.e2e.fixtures.fake_mcp_pe"


def plan_step(
    *,
    step_id: int,
    stage: int,
    tool: str,
    args: dict[str, Any] | None = None,
    description: str = "step",
    success_criterion: str = "ok",
    depends_on: list[int] | None = None,
) -> dict[str, Any]:
    """Build a plan step dict for use in a planner mock response."""
    return {
        "id": step_id,
        "stage": stage,
        "description": description,
        "tool": tool,
        "args": args or {},
        "success_criterion": success_criterion,
        "depends_on": depends_on or [],
    }


def plan_json(*, goal: str, steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the planner mock JSON envelope."""
    return {"goal": goal, "steps": steps}


def add_planner_response(
    stub: StubLlm,
    plan: dict[str, Any],
    *,
    prompt_tokens: int = 5,
    completion_tokens: int = 5,
    cost: float | None = 0.0001,
) -> None:
    """Append a planner response (JSON content) to a ``StubLlm``."""
    stub.add_text(
        json.dumps(plan),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )


def add_step_response(
    stub: StubLlm,
    *,
    step_id: int,
    status: str = "success",
    output: str = "",
    notes: str = "",
    reason: str = "",
    prompt_tokens: int = 5,
    completion_tokens: int = 5,
    cost: float | None = 0.0001,
) -> None:
    """Append an executor-step JSON response to a ``StubLlm``."""
    payload: dict[str, Any] = {
        "step_id": step_id,
        "status": status,
        "output": output,
        "notes": notes,
    }
    if reason:
        payload["reason"] = reason
    stub.add_text(
        json.dumps(payload),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )


def add_synthesis_response(
    stub: StubLlm,
    *,
    text: str,
    prompt_tokens: int = 5,
    completion_tokens: int = 5,
    cost: float | None = 0.0001,
) -> None:
    """Append the final synthesis text response to a ``StubLlm``."""
    stub.add_text(
        text,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )


def add_raw_text(
    stub: StubLlm,
    *,
    content: str,
    prompt_tokens: int = 5,
    completion_tokens: int = 5,
    cost: float | None = 0.0001,
) -> None:
    """Append a raw, non-JSON LLM response (used to simulate planner garbage)."""
    stub.add_text(
        content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost=cost,
    )


def encrypt_env(env: dict[str, str]) -> bytes:
    """Encrypt an env dict with the test ``LCNC_A2A_ENCRYPTION_KEY``."""
    fernet = Fernet(os.environ["LCNC_A2A_ENCRYPTION_KEY"].encode())
    return fernet.encrypt(json.dumps(env, sort_keys=True).encode("utf-8"))


async def seed_started_pe_agent(
    db_engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    name: str = "agent-PE",
    description: str = "PE test agent",
    model_endpoint: str = LLM_ENDPOINT,
    model_provider: str = "openrouter",
    max_steps: int = 20,
    max_tokens: int = 16000,
    planner_prompt: str = "You are the planner. Output a strict JSON plan only.",
    executor_prompt: str = "You are the executor. Output a strict JSON envelope only.",
) -> tuple[uuid.UUID, str]:
    """Insert a started plan_execute agent + one API key. Return ``(agent_id, plain_key)``."""
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
                "max_steps, planner_prompt, executor_prompt, status) VALUES (:id, :user_id, "
                ":name, :description, 'plan_execute', :model_provider, :model_endpoint, "
                "'mock-model', :enc, 1, :max_tokens, :max_steps, :planner, :executor, "
                "'started')"
            ),
            {
                "id": agent_id,
                "user_id": user_id,
                "name": name,
                "description": description,
                "model_provider": model_provider,
                "model_endpoint": model_endpoint,
                "enc": enc_key,
                "max_tokens": max_tokens,
                "max_steps": max_steps,
                "planner": planner_prompt,
                "executor": executor_prompt,
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


def tool_descriptor(name: str) -> dict[str, Any]:
    """Build a minimal MCP tool descriptor for tools_cache seeding."""
    return {
        "name": name,
        "description": f"Stub {name} tool.",
        "input_schema": {"type": "object", "properties": {}},
    }


async def seed_pe_mcp(
    db_engine: AsyncEngine,
    *,
    agent_id: uuid.UUID,
    tool_names: list[str],
    env: dict[str, str] | None = None,
) -> uuid.UUID:
    """Insert an MCP stdio server pointing at the PE fake fixture."""
    server_id = uuid.uuid4()
    cache = {"tools": [tool_descriptor(name) for name in tool_names]}
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, command, "
                "tools_cache, env_enc, discovered_at) VALUES (:id, :agent_id, 'stdio', "
                ":command, :cache, :env_enc, :now)"
            ),
            {
                "id": server_id,
                "agent_id": agent_id,
                "command": FAKE_MCP_PE_CMD,
                "cache": json.dumps(cache),
                "env_enc": encrypt_env(env or {}),
                "now": datetime.now(UTC),
            },
        )
    return server_id


__all__ = [
    "FAKE_MCP_PE_CMD",
    "add_planner_response",
    "add_raw_text",
    "add_step_response",
    "add_synthesis_response",
    "encrypt_env",
    "plan_json",
    "plan_step",
    "seed_pe_mcp",
    "seed_started_pe_agent",
    "tool_descriptor",
]
