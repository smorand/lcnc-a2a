"""US-005 acceptance tests for A2A bearer-key authentication (E2E-050, 051, 089, 094, 095)."""

from __future__ import annotations

import statistics
import time

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import make_a2a_envelope, seed_started_agent


@pytest.mark.asyncio
async def test_e2e_050_post_without_bearer_returns_401(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, _ = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    response = await http_client.post(
        f"/agents/{agent_id}/message:stream",
        json=make_a2a_envelope("hi"),
    )
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"error": "auth_required"}


@pytest.mark.asyncio
async def test_e2e_051_key_from_other_agent_returns_403(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent1_id, _key1 = await seed_started_agent(db_engine, user_id=user_id, name="agent-A1")
    _agent2_id, key2 = await seed_started_agent(db_engine, user_id=user_id, name="agent-A2")
    response = await http_client.post(
        f"/agents/{agent1_id}/message:stream",
        json=make_a2a_envelope("hi"),
        headers={"Authorization": f"Bearer {key2}"},
    )
    assert response.status_code == 403
    assert response.json() == {"error": "auth_invalid"}


@pytest.mark.asyncio
async def test_e2e_094_cross_user_isolation_via_api_key(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    alice_id = await seed_user("alice@example.com", "Alice")
    bob_id = await seed_user("bob@example.com", "Bob")
    agent_a_id, _ = await seed_started_agent(db_engine, user_id=alice_id, name="agent-A")
    _agent_b_id, key_b = await seed_started_agent(db_engine, user_id=bob_id, name="agent-B")
    response = await http_client.post(
        f"/agents/{agent_a_id}/message:stream",
        json=make_a2a_envelope("hi"),
        headers={"Authorization": f"Bearer {key_b}"},
    )
    assert response.status_code == 403
    assert response.json() == {"error": "auth_invalid"}


@pytest.mark.asyncio
async def test_e2e_089_anonymous_to_stopped_returns_401(
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
    response = await http_client.post(
        f"/agents/{agent_id}/message:stream",
        json=make_a2a_envelope("hi"),
    )
    assert response.status_code == 401
    assert response.json() == {"error": "auth_required"}


@pytest.mark.asyncio
async def test_e2e_095_constant_time_api_key_comparison(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-K")
    # Build invalid keys differing at byte 0 vs byte -1.
    other_first = "z" + plain[1:]
    other_last = plain[:-1] + ("Z" if plain[-1] != "Z" else "Y")
    durations_first: list[float] = []
    durations_last: list[float] = []
    iterations = 60
    for i in range(iterations):
        target = other_first if i % 2 == 0 else other_last
        start = time.perf_counter()
        response = await http_client.post(
            f"/agents/{agent_id}/message:stream",
            json=make_a2a_envelope("hi"),
            headers={"Authorization": f"Bearer {target}"},
        )
        elapsed = time.perf_counter() - start
        assert response.status_code in (401, 403)
        (durations_first if i % 2 == 0 else durations_last).append(elapsed)
    median_first = statistics.median(durations_first)
    median_last = statistics.median(durations_last)
    # Loose bound: with `==` we'd see orders-of-magnitude divergence on average.
    assert max(median_first, median_last) <= 5 * min(median_first, median_last) + 0.005
