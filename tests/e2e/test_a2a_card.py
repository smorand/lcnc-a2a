"""US-005 acceptance tests for the Agent Card endpoint (E2E-025, 086, 087, 088)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import seed_started_agent


@pytest.mark.asyncio
async def test_e2e_086_agent_card_started(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, _ = await seed_started_agent(
        db_engine,
        user_id=user_id,
        name="fin-analyst",
        mode="react",
        description="Financial analyst",
    )
    response = await http_client.get(f"/agents/{agent_id}/.well-known/agent-card.json")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["name"] == "fin-analyst"
    assert payload["description"] == "Financial analyst"
    assert payload["version"] == "1.0"
    assert payload["url"].endswith(f"/agents/{agent_id}")
    assert payload["capabilities"] == {"streaming": True, "pushNotifications": False}
    assert payload["securitySchemes"] == {"bearer_api_key": {"type": "http", "scheme": "bearer"}}
    assert payload["skills"] == [{"name": "fin-analyst", "description": "Financial analyst", "tags": ["react"]}]
    assert payload["defaultInputModes"] == ["text"]
    assert payload["defaultOutputModes"] == ["text"]


@pytest.mark.asyncio
async def test_e2e_087_agent_card_stopped_returns_503(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, _ = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE agents SET status = 'stopped' WHERE id = :id"),
            {"id": agent_id},
        )
    response = await http_client.get(f"/agents/{agent_id}/.well-known/agent-card.json")
    assert response.status_code == 503
    assert response.json() == {"error": "agent_stopped"}


@pytest.mark.asyncio
async def test_e2e_088_agent_card_unknown_agent_returns_404(http_client: httpx.AsyncClient) -> None:
    random_id = uuid.uuid4()
    response = await http_client.get(f"/agents/{random_id}/.well-known/agent-card.json")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_e2e_025_start_makes_card_active(
    login_as,
    seed_user,
    db_engine: AsyncEngine,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    client = await login_as("alice@example.com", name="Alice")
    agent_id, _ = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE agents SET status = 'stopped' WHERE id = :id"),
            {"id": agent_id},
        )
    edit = await client.get(f"/agents/{agent_id}/edit")
    assert edit.status_code == 200
    import re

    csrf = re.search(r'name="csrf_token"\s+value="([^"]+)"', edit.text).group(1)  # type: ignore[union-attr]
    response = await client.post(
        f"/agents/{agent_id}/start",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 302
    async with db_engine.begin() as conn:
        status = (
            await conn.execute(
                text("SELECT status FROM agents WHERE id = :id"),
                {"id": agent_id},
            )
        ).scalar_one()
    assert status == "started"

    card_response = await client.get(f"/agents/{agent_id}/.well-known/agent-card.json")
    assert card_response.status_code == 200
    assert card_response.headers["content-type"].startswith("application/json")
