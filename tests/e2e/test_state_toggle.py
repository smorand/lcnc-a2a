"""Acceptance tests for the start/stop state toggle (E2E-025, E2E-027)."""

from __future__ import annotations

import re
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def test_e2e_025_start_flips_status_to_started(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
    fetch_user_id,
    seed_agent,
) -> None:
    """E2E-025: POST /agents/<id>/start sets status to 'started'."""
    client = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="agent-a", status="stopped")

    response = await client.post(
        f"/agents/{agent_id}/start",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 302, response.text

    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT status FROM agents WHERE id = :id"), {"id": agent_id})
        assert row.scalar_one() == "started"


async def test_e2e_027_idempotent_state_transitions(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
    fetch_user_id,
    seed_agent,
) -> None:
    """E2E-027: started→started, stopped→stopped are idempotent (302 + status unchanged)."""
    client = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="agent-a", status="started")

    response = await client.post(
        f"/agents/{agent_id}/start",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 302
    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT status FROM agents WHERE id = :id"), {"id": agent_id})
        assert row.scalar_one() == "started"

    first = await client.post(f"/agents/{agent_id}/stop", data={"csrf_token": csrf_token})
    second = await client.post(f"/agents/{agent_id}/stop", data={"csrf_token": csrf_token})
    assert first.status_code == 302
    assert second.status_code == 302
    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT status FROM agents WHERE id = :id"), {"id": agent_id})
        assert row.scalar_one() == "stopped"


async def test_e2e_state_toggle_404_for_other_user(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
    fetch_user_id,
    seed_agent,
) -> None:
    """A non-owner gets 404 for start/stop and the status is unchanged."""
    await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="agent-a", status="stopped")

    bob = await login_as("bob@example.com", name="Bob")
    bob_csrf = await _csrf(bob)

    start_resp = await bob.post(f"/agents/{agent_id}/start", data={"csrf_token": bob_csrf})
    assert start_resp.status_code == 404
    stop_resp = await bob.post(f"/agents/{agent_id}/stop", data={"csrf_token": bob_csrf})
    assert stop_resp.status_code == 404

    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT status FROM agents WHERE id = :id"), {"id": agent_id})
        assert row.scalar_one() == "stopped"


async def test_e2e_state_toggle_unknown_id_404(
    login_as,
    csrf_token: str,
) -> None:
    """An unknown agent id returns 404."""
    client = await login_as("alice@example.com", name="Alice")
    response = await client.post(
        f"/agents/{uuid.uuid4()}/start",
        data={"csrf_token": csrf_token},
    )
    assert response.status_code == 404
