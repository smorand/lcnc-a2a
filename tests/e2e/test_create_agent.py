"""Acceptance tests for the create-agent flow (E2E-012..016, E2E-019)."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

REACT_PROMPT = "You are a ReAct agent. Think step-by-step, choose tools wisely, and stop when the answer is sufficient."


async def test_e2e_012_create_agent_happy_path(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
    fetch_user_id,
) -> None:
    """E2E-012: POST /agents creates the agent + key; GET /agents/<id> shows it once."""
    client: httpx.AsyncClient = await login_as("alice@example.com", name="Alice")
    user_id = await fetch_user_id("alice@example.com")

    response = await client.post(
        "/agents",
        data={
            "name": "fin-analyst",
            "description": "Public co analyst",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "sk-or-v1-test",
            "system_prompt": REACT_PROMPT,
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": csrf_token,
        },
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith("/agents/")
    new_id = uuid.UUID(location.rsplit("/", 1)[-1])

    detail = await client.get(location)
    assert detail.status_code == 200
    body = detail.text
    assert "data-copy-target" in body

    # Locate exactly one 43-char base64url key in the body
    key_matches = re.findall(r"[A-Za-z0-9_-]{43}", body)
    # Filter to keys that match the copy target attribute literally
    plain_keys = re.findall(r'data-copy-target="([A-Za-z0-9_-]{43})"', body)
    assert len(plain_keys) == 1, body
    plain_key = plain_keys[0]
    assert plain_key in key_matches

    async with db_engine.begin() as conn:
        agent_row = await conn.execute(
            text(
                "SELECT id, user_id, name, description, mode, model_provider, "
                "model_endpoint, model_id, provider_api_key_enc, status, "
                "created_at, max_loops, max_tokens, similarity_threshold "
                "FROM agents"
            )
        )
        rows = list(agent_row.mappings())
        assert len(rows) == 1
        agent = rows[0]
        assert agent["id"] == new_id
        assert agent["user_id"] == user_id
        assert agent["name"] == "fin-analyst"
        assert agent["description"] == "Public co analyst"
        assert agent["mode"] == "react"
        assert agent["status"] == "stopped"
        assert agent["model_id"] == "anthropic/claude-sonnet-4-5"
        assert agent["max_loops"] == 10
        assert agent["max_tokens"] == 8000
        assert agent["similarity_threshold"] == 0.95
        # provider_api_key encrypted
        enc = bytes(agent["provider_api_key_enc"])
        assert enc != b"sk-or-v1-test"
        # created_at within the last 5 seconds
        now = datetime.now(UTC)
        assert agent["created_at"] >= now - timedelta(seconds=5)

        keys = await conn.execute(text("SELECT label, key_hash, key_last4 FROM agent_api_keys"))
        key_rows = list(keys.mappings())
        assert len(key_rows) == 1
        assert key_rows[0]["label"] == "default"
        assert bytes(key_rows[0]["key_hash"]) == hashlib.sha256(plain_key.encode()).digest()
        assert key_rows[0]["key_last4"] == plain_key[-4:]

    # Decrypt provider key via the live crypto service.
    from lcnc_a2a.crypto import CryptoService

    settings_key = client.headers.get("x-test-only", "")
    # Easier path: read app state directly via the running app instance.
    # In the test harness, the app is built per test; recover it via httpx ASGI transport.
    transport = client._transport
    asgi_app = transport.app  # type: ignore[attr-defined]
    crypto: CryptoService = asgi_app.state.crypto
    assert crypto.decrypt(enc).decode() == "sk-or-v1-test"
    _ = settings_key


async def test_e2e_013_missing_name(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-013: missing name → 400 with `name_required` and no rows inserted."""
    client = await login_as("alice@example.com", name="Alice")

    response = await client.post(
        "/agents",
        data={
            "name": "",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "sk-or-v1-test",
            "system_prompt": REACT_PROMPT,
            "csrf_token": csrf_token,
        },
    )
    assert response.status_code == 400
    assert "name_required" in response.text

    async with db_engine.begin() as conn:
        rows = await conn.execute(text("SELECT count(*) FROM agents"))
        assert rows.scalar_one() == 0


async def test_e2e_014_duplicate_name(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
    fetch_user_id,
    seed_agent,
) -> None:
    """E2E-014: duplicate (user_id, name) → 400 with `name_taken`; no second row."""
    client = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    await seed_agent(alice_id, name="fin-analyst")

    response = await client.post(
        "/agents",
        data={
            "name": "fin-analyst",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "sk-or-v1-test",
            "system_prompt": REACT_PROMPT,
            "csrf_token": csrf_token,
        },
    )
    assert response.status_code == 400, response.text
    assert "name_taken" in response.text

    async with db_engine.begin() as conn:
        rows = await conn.execute(
            text("SELECT count(*) FROM agents WHERE user_id = :uid AND name = 'fin-analyst'"),
            {"uid": alice_id},
        )
        assert rows.scalar_one() == 1


async def test_e2e_015_pe_missing_prompts(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-015: PE mode without planner / executor prompts → `prompts_required`."""
    client = await login_as("alice@example.com", name="Alice")

    response = await client.post(
        "/agents",
        data={
            "name": "pe-agent",
            "mode": "plan_execute",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "sk-or-v1-test",
            "planner_prompt": "",
            "executor_prompt": "exec",
            "max_steps": "20",
            "csrf_token": csrf_token,
        },
    )
    assert response.status_code == 400
    assert "prompts_required" in response.text

    async with db_engine.begin() as conn:
        rows = await conn.execute(text("SELECT count(*) FROM agents"))
        assert rows.scalar_one() == 0


async def test_e2e_016_max_steps_out_of_range(
    login_as,
    csrf_token: str,
) -> None:
    """E2E-016: PE with `max_steps = 51` → `max_steps_out_of_range`."""
    client = await login_as("alice@example.com", name="Alice")

    response = await client.post(
        "/agents",
        data={
            "name": "pe-agent",
            "mode": "plan_execute",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "sk-or-v1-test",
            "planner_prompt": "plan",
            "executor_prompt": "exec",
            "max_steps": "51",
            "csrf_token": csrf_token,
        },
    )
    assert response.status_code == 400
    assert "max_steps_out_of_range" in response.text


async def test_e2e_019_unicode_name_and_prompt_persisted(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-019: Unicode round-trip in name and system_prompt."""
    client = await login_as("alice@example.com", name="Alice")

    response = await client.post(
        "/agents",
        data={
            "name": "アナリスト 📊",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "sk-or-v1-test",
            "system_prompt": "あなたは…",
            "csrf_token": csrf_token,
        },
    )
    assert response.status_code == 302, response.text

    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT name, system_prompt FROM agents"))
        record = row.mappings().one()
        assert record["name"] == "アナリスト 📊"
        assert record["system_prompt"] == "あなたは…"
