"""Acceptance tests for US-002 dashboard (E2E-006..010, E2E-102)."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx


async def test_e2e_006_dashboard_shows_only_current_user_agents(
    login_as,
    fetch_user_id,
    seed_agent,
    seed_run,
) -> None:
    """E2E-006: alice's dashboard contains alice agents only, with metrics."""
    # Both users must exist; create bob first, then login as alice.
    client_bob: httpx.AsyncClient = await login_as("bob@example.com", name="Bob")
    bob_id = await fetch_user_id("bob@example.com")
    bob_agent = await seed_agent(bob_id, name="bob-agent-1")

    # Switch to alice (clear cookies first to drop bob's session)
    client_bob.cookies.clear()
    client_alice: httpx.AsyncClient = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    a1 = await seed_agent(alice_id, name="alice-agent-1")
    a2 = await seed_agent(alice_id, name="alice-agent-2")

    now = datetime.now(UTC)
    for agent_id in (a1, a2, bob_agent):
        for _ in range(3):
            await seed_run(
                agent_id,
                started_at=now,
                tokens_in=100,
                tokens_out=200,
                duration_ms=500,
                loops=2,
                cost_usd=Decimal("0.001"),
            )

    response = await client_alice.get("/agents")
    assert response.status_code == 200, response.text
    body = response.text
    assert "alice-agent-1" in body
    assert "alice-agent-2" in body
    assert "bob-agent-1" not in body

    rows = re.findall(r'class="agent-row"', body)
    assert len(rows) == 2


async def test_e2e_007_empty_state_for_user_with_no_agents(login_as) -> None:
    """E2E-007: zero-agent user sees empty state with the literal `Create agent`."""
    client = await login_as("solo@example.com", name="Solo")

    response = await client.get("/agents")
    assert response.status_code == 200
    body = response.text
    assert "Create agent" in body
    assert "No agents yet" in body
    assert 'class="agent-row"' not in body


async def test_e2e_008_aggregations_only_in_window(
    login_as,
    fetch_user_id,
    seed_agent,
    seed_run,
) -> None:
    """E2E-008: runs older than 30 days are excluded from the aggregation."""
    client = await login_as("ada@example.com", name="Ada")
    user_id = await fetch_user_id("ada@example.com")
    agent_id = await seed_agent(user_id, name="agent-A")

    now = datetime.now(UTC)
    old = now - timedelta(days=60)
    for _ in range(3):
        await seed_run(
            agent_id, started_at=now, tokens_in=10, tokens_out=20, duration_ms=100, loops=1, cost_usd=Decimal("0.001")
        )
    for _ in range(2):
        await seed_run(
            agent_id, started_at=old, tokens_in=10, tokens_out=20, duration_ms=100, loops=1, cost_usd=Decimal("0.001")
        )

    response = await client.get("/agents")
    assert response.status_code == 200
    match = re.search(r'class="agent-cell-requests">\s*(\d+)', response.text)
    assert match is not None, response.text
    assert match.group(1) == "3"


async def test_e2e_009_counters_include_failed_runs(
    login_as,
    fetch_user_id,
    seed_agent,
    seed_run,
) -> None:
    """E2E-009: failed runs still contribute to the requests / token counters."""
    client = await login_as("alice@example.com", name="Alice")
    user_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(user_id, name="alice-agent")

    now = datetime.now(UTC)
    await seed_run(
        agent_id,
        started_at=now,
        status="completed",
        tokens_in=100,
        tokens_out=200,
        duration_ms=500,
        loops=1,
        cost_usd=Decimal("0.001"),
    )
    await seed_run(
        agent_id,
        started_at=now,
        status="failed",
        tokens_in=50,
        tokens_out=0,
        duration_ms=200,
        loops=1,
        cost_usd=Decimal("0.001"),
    )

    response = await client.get("/agents")
    assert response.status_code == 200
    body = response.text

    requests = re.search(r'class="agent-cell-requests">\s*(\d+)', body)
    tokens_in = re.search(r'class="agent-cell-tokens-in">\s*(\d+)', body)
    tokens_out = re.search(r'class="agent-cell-tokens-out">\s*(\d+)', body)

    assert requests is not None and requests.group(1) == "2"
    assert tokens_in is not None and tokens_in.group(1) == "150"
    assert tokens_out is not None and tokens_out.group(1) == "200"


async def test_e2e_010_cost_renders_na_when_any_run_lacks_cost(
    login_as,
    fetch_user_id,
    seed_agent,
    seed_run,
) -> None:
    """E2E-010: any NULL cost in the window forces the cost cell to `n/a`."""
    client = await login_as("alice@example.com", name="Alice")
    user_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(user_id, name="cost-agent")

    now = datetime.now(UTC)
    await seed_run(
        agent_id, started_at=now, tokens_in=10, tokens_out=10, duration_ms=100, loops=1, cost_usd=Decimal("0.012")
    )
    await seed_run(agent_id, started_at=now, tokens_in=10, tokens_out=10, duration_ms=100, loops=1, cost_usd=None)

    response = await client.get("/agents")
    assert response.status_code == 200
    cost_match = re.search(r'class="agent-cell-cost">\s*([^\s<]+)', response.text)
    assert cost_match is not None, response.text
    assert cost_match.group(1) == "n/a"


async def test_e2e_102_dashboard_perf_baseline(
    login_as,
    fetch_user_id,
    perf_seed,
) -> None:
    """E2E-102: p95 latency over 20 measured GET /agents calls < 500 ms."""
    client = await login_as("alice@example.com", name="Alice")
    user_id = await fetch_user_id("alice@example.com")
    await perf_seed(user_id)

    # Warm up
    response = await client.get("/agents")
    assert response.status_code == 200

    timings: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        response = await client.get("/agents")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert response.status_code == 200
        timings.append(elapsed_ms)

    timings.sort()
    p95 = timings[int(0.95 * len(timings)) - 1]
    assert p95 < 500, f"p95 latency {p95:.1f} ms exceeds 500 ms; samples: {timings}"
