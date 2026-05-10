"""Acceptance tests for the edit-agent flow (E2E-020..024)."""

from __future__ import annotations

import re
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

REACT_PROMPT = "ReAct prompt for tests"


async def _csrf_for(client: httpx.AsyncClient, agent_id: uuid.UUID) -> str:
    response = await client.get(f"/agents/{agent_id}/edit")
    assert response.status_code == 200, response.text
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def _create_agent(
    client: httpx.AsyncClient,
    *,
    csrf: str,
    name: str = "fin-analyst",
    provider_key: str = "K1",
) -> uuid.UUID:
    response = await client.post(
        "/agents",
        data={
            "name": name,
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": provider_key,
            "system_prompt": "old",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 302, response.text
    return uuid.UUID(response.headers["location"].rsplit("/", 1)[-1])


async def test_e2e_020_edit_happy_path_blank_api_key(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-020: blank API key on edit leaves provider_api_key_enc unchanged byte-for-byte."""
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(client, csrf=csrf_token, name="agent-a")

    async with db_engine.begin() as conn:
        before = await conn.execute(
            text("SELECT provider_api_key_enc, system_prompt, updated_at FROM agents WHERE id = :id"),
            {"id": agent_id},
        )
        row = before.mappings().one()
        enc_before = bytes(row["provider_api_key_enc"])
        assert row["system_prompt"] == "old"
        updated_before = row["updated_at"]

    edit_csrf = await _csrf_for(client, agent_id)
    response = await client.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-a",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "",
            "system_prompt": "new",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": edit_csrf,
        },
    )
    assert response.status_code == 302, response.text
    assert response.headers["location"] == f"/agents/{agent_id}"

    async with db_engine.begin() as conn:
        after = await conn.execute(
            text("SELECT provider_api_key_enc, system_prompt, updated_at FROM agents WHERE id = :id"),
            {"id": agent_id},
        )
        row = after.mappings().one()
        assert row["system_prompt"] == "new"
        assert bytes(row["provider_api_key_enc"]) == enc_before
        assert row["updated_at"] > updated_before


async def test_e2e_021_edit_returns_404_for_other_users_agent(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-021: editing another user's agent returns 404 with no mutation."""
    alice = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(alice, csrf=csrf_token, name="agent-a")

    async with db_engine.begin() as conn:
        before = await conn.execute(text("SELECT system_prompt FROM agents WHERE id = :id"), {"id": agent_id})
        prompt_before = before.scalar_one()

    bob = await login_as("bob@example.com", name="Bob")
    bob_csrf_resp = await bob.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', bob_csrf_resp.text)
    assert match is not None
    bob_csrf = match.group(1)

    response = await bob.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-a",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "",
            "system_prompt": "hijacked",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": bob_csrf,
        },
    )
    assert response.status_code == 404

    async with db_engine.begin() as conn:
        after = await conn.execute(text("SELECT system_prompt FROM agents WHERE id = :id"), {"id": agent_id})
        assert after.scalar_one() == prompt_before


async def test_e2e_022_edit_rejects_pe_without_prompts(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-022: switching to plan_execute without both prompts returns 400 prompts_required; mode unchanged."""
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(client, csrf=csrf_token, name="agent-a")

    edit_csrf = await _csrf_for(client, agent_id)
    response = await client.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-a",
            "mode": "plan_execute",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "",
            "planner_prompt": "",
            "executor_prompt": "",
            "max_steps": "20",
            "csrf_token": edit_csrf,
        },
    )
    assert response.status_code == 400
    assert "prompts_required" in response.text

    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT mode FROM agents WHERE id = :id"), {"id": agent_id})
        assert row.scalar_one() == "react"


async def test_e2e_023_edit_replacing_provider_key_reencrypts(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-023: supplying a new provider API key re-encrypts and decrypts to that value."""
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(client, csrf=csrf_token, name="agent-a", provider_key="K1")

    async with db_engine.begin() as conn:
        before = await conn.execute(text("SELECT provider_api_key_enc FROM agents WHERE id = :id"), {"id": agent_id})
        enc_before = bytes(before.scalar_one())

    edit_csrf = await _csrf_for(client, agent_id)
    response = await client.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-a",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "K2",
            "system_prompt": "old",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": edit_csrf,
        },
    )
    assert response.status_code == 302, response.text

    async with db_engine.begin() as conn:
        after = await conn.execute(text("SELECT provider_api_key_enc FROM agents WHERE id = :id"), {"id": agent_id})
        enc_after = bytes(after.scalar_one())

    assert enc_after != enc_before
    transport = client._transport
    asgi_app = transport.app  # type: ignore[attr-defined]
    crypto = asgi_app.state.crypto
    assert crypto.decrypt(enc_after).decode() == "K2"


async def test_e2e_024_edit_preserves_run_history(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
    seed_run,
) -> None:
    """E2E-024: editing the agent leaves agent_runs rows byte-identical."""
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(client, csrf=csrf_token, name="agent-a")

    for _ in range(5):
        await seed_run(agent_id, status="completed")

    async with db_engine.begin() as conn:
        rows_before = list(
            (
                await conn.execute(
                    text(
                        "SELECT id, agent_id, status, started_at, duration_ms, loops, "
                        "tokens_in, tokens_out, cost_usd FROM agent_runs WHERE agent_id = :id "
                        "ORDER BY id"
                    ),
                    {"id": agent_id},
                )
            ).mappings()
        )
        assert len(rows_before) == 5

    edit_csrf = await _csrf_for(client, agent_id)
    response = await client.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-a",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "",
            "system_prompt": "edited",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": edit_csrf,
        },
    )
    assert response.status_code == 302, response.text

    async with db_engine.begin() as conn:
        rows_after = list(
            (
                await conn.execute(
                    text(
                        "SELECT id, agent_id, status, started_at, duration_ms, loops, "
                        "tokens_in, tokens_out, cost_usd FROM agent_runs WHERE agent_id = :id "
                        "ORDER BY id"
                    ),
                    {"id": agent_id},
                )
            ).mappings()
        )
        assert len(rows_after) == 5

    serialized_before = [tuple(r.items()) for r in rows_before]
    serialized_after = [tuple(r.items()) for r in rows_after]
    assert serialized_before == serialized_after
    _ = REACT_PROMPT


async def test_edit_agent_attaches_mcp_catalog_presets(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """Save changes with mcp_preset_ids attaches the corresponding catalog servers; existing rows stay; duplicates are skipped."""
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(client, csrf=csrf_token, name="agent-a")

    edit_csrf = await _csrf_for(client, agent_id)
    response = await client.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-a",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "",
            "system_prompt": "still here",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": edit_csrf,
            # Two catalog presets (one duplicate to test idempotency on second save below).
            "mcp_preset_ids": ["duckduckgo", "fetch"],
        },
    )
    assert response.status_code == 302, response.text

    async with db_engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT transport, command, url FROM agent_mcp_servers "
                "WHERE agent_id = :aid ORDER BY transport, command"
            ),
            {"aid": str(agent_id)},
        )
        attached = [(r["transport"], r["command"], r["url"]) for r in rows.mappings()]
    assert ("stdio", "uvx duckduckgo-mcp-server", None) in attached
    assert ("stdio", "uvx mcp-server-fetch", None) in attached
    assert len(attached) == 2

    # Re-saving with the same selection must not create duplicates.
    edit_csrf = await _csrf_for(client, agent_id)
    response = await client.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-a",
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "",
            "system_prompt": "still here",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": edit_csrf,
            "mcp_preset_ids": ["duckduckgo", "fetch", "context7"],
        },
    )
    assert response.status_code == 302, response.text

    async with db_engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT transport, command, url FROM agent_mcp_servers "
                "WHERE agent_id = :aid ORDER BY transport, command"
            ),
            {"aid": str(agent_id)},
        )
        attached = [(r["transport"], r["command"], r["url"]) for r in rows.mappings()]
    # DDG + fetch from first save (deduplicated), Context7 newly added.
    assert ("stdio", "uvx duckduckgo-mcp-server", None) in attached
    assert ("stdio", "uvx mcp-server-fetch", None) in attached
    assert ("streamable_http", None, "https://mcp.context7.com/mcp") in attached
    assert len(attached) == 3
