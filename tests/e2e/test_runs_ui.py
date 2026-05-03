"""Acceptance tests for US-008 runs history & per-run trace UI (E2E-043..047)."""

from __future__ import annotations

import html
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _seed_run_full(
    db_engine: AsyncEngine,
    *,
    agent_id: uuid.UUID,
    started_at: datetime,
    status: str = "completed",
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    duration_ms: int | None = None,
    loops: int | None = None,
    cost_usd: Decimal | None = None,
    final_answer: str | None = None,
    a2a_task_id: str | None = None,
    context_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert an ``agent_runs`` row with the columns we need to render."""
    run_id = uuid.uuid4()
    async with db_engine.begin() as conn:
        if context_id is not None:
            await conn.execute(
                text("INSERT INTO agent_contexts (id, agent_id) VALUES (:id, :agent_id)"),
                {"id": context_id, "agent_id": agent_id},
            )
        await conn.execute(
            text(
                "INSERT INTO agent_runs (id, agent_id, status, started_at, "
                "duration_ms, loops, tokens_in, tokens_out, cost_usd, final_answer, "
                "a2a_task_id, context_id) VALUES (:id, :agent_id, :status, "
                ":started_at, :duration_ms, :loops, :tokens_in, :tokens_out, "
                ":cost_usd, :final_answer, :a2a_task_id, :context_id)",
            ),
            {
                "id": run_id,
                "agent_id": agent_id,
                "status": status,
                "started_at": started_at,
                "duration_ms": duration_ms,
                "loops": loops,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
                "final_answer": final_answer,
                "a2a_task_id": a2a_task_id,
                "context_id": context_id,
            },
        )
    return run_id


async def _seed_step(
    db_engine: AsyncEngine,
    *,
    run_id: uuid.UUID,
    seq: int,
    role: str,
    content: str | None = None,
    tool_name: str | None = None,
    tool_args_json: Any | None = None,
    tool_result_json: Any | None = None,
) -> uuid.UUID:
    step_id = uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_run_steps (id, run_id, seq, role, content, "
                "tool_name, tool_args_json, tool_result_json) VALUES "
                "(:id, :run_id, :seq, :role, :content, :tool_name, "
                "CAST(:tool_args_json AS JSONB), CAST(:tool_result_json AS JSONB))",
            ),
            {
                "id": step_id,
                "run_id": run_id,
                "seq": seq,
                "role": role,
                "content": content,
                "tool_name": tool_name,
                "tool_args_json": json.dumps(tool_args_json) if tool_args_json is not None else None,
                "tool_result_json": json.dumps(tool_result_json) if tool_result_json is not None else None,
            },
        )
    return step_id


def _build_5kb_payload() -> dict[str, str]:
    """Return a dict whose ``json.dumps`` is exactly 5 KB (5120 chars)."""
    target = 5120
    base = '{"data": ""}'  # 12 chars overhead
    filler = "x" * (target - len(base))
    payload = {"data": filler}
    encoded = json.dumps(payload)
    assert len(encoded) == target, f"got {len(encoded)} not {target}"
    return payload


async def test_e2e_043_view_runs_list_happy_path(
    login_as,
    fetch_user_id,
    seed_agent,
    db_engine: AsyncEngine,
) -> None:
    """E2E-043: list shows 3 rows ordered by started_at DESC, with summary truncation."""
    client = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="alice-agent")

    now = datetime.now(UTC)
    answers = [
        "A" * 100,  # >= 90 chars -> summary truncates at 80
        "B" * 95,
        "C" * 120,
    ]
    started_offsets = [0, 60, 120]  # seconds; first is most recent
    run_ids: list[uuid.UUID] = []
    for i, (ans, off) in enumerate(zip(answers, started_offsets, strict=True)):
        rid = await _seed_run_full(
            db_engine,
            agent_id=agent_id,
            started_at=now - timedelta(seconds=off),
            status="completed",
            tokens_in=100 + i,
            tokens_out=200 + i,
            duration_ms=500 + i,
            loops=2,
            cost_usd=Decimal("0.001234"),
            final_answer=ans,
        )
        run_ids.append(rid)

    response = await client.get(f"/agents/{agent_id}/runs")
    assert response.status_code == 200, response.text
    body = response.text

    rows = re.findall(r'<tr class="run-row"[^>]*data-run-id="([0-9a-f-]+)"', body)
    assert len(rows) == 3, body

    # Rows are ordered started_at DESC: most recent first => run_ids[0]
    expected_order = [str(rid) for rid in run_ids]
    assert rows == expected_order, (rows, expected_order)

    # Each run renders tokens, cost, status, duration, and 80-char summary + ellipsis
    for i, ans in enumerate(answers):
        token_in = str(100 + i)
        token_out = str(200 + i)
        duration = str(500 + i)
        first80 = ans[:80]
        assert f'class="run-cell-tokens-in">{token_in}' in body, f"missing tokens_in {token_in}"
        assert f'class="run-cell-tokens-out">{token_out}' in body, f"missing tokens_out {token_out}"
        assert f'class="run-cell-duration">{duration}' in body, body
        assert first80 + "…" in body, f"summary truncation missing for run {i}"

    # Cost rendered with $ prefix and 6 decimals
    assert "$0.001234" in body
    # Status rendered
    assert 'class="run-cell-status">completed' in body


async def test_e2e_044_runs_list_404_for_other_user(
    login_as,
    fetch_user_id,
    seed_agent,
) -> None:
    """E2E-044: bob cannot view alice's runs list."""
    client_alice = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="alice-agent")

    client_alice.cookies.clear()
    client_bob = await login_as("bob@example.com", name="Bob")

    response = await client_bob.get(f"/agents/{agent_id}/runs")
    assert response.status_code == 404, response.text


async def test_e2e_045_runs_list_empty_state(
    login_as,
    fetch_user_id,
    seed_agent,
) -> None:
    """E2E-045: empty runs list shows the literal hint with the agent id."""
    client = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="alice-agent")

    response = await client.get(f"/agents/{agent_id}/runs")
    assert response.status_code == 200, response.text
    expected = f"Send a message to /agents/{agent_id} to see runs here."
    assert expected in response.text, response.text


async def test_e2e_046_truncated_payload_and_full_view(
    login_as,
    fetch_user_id,
    seed_agent,
    db_engine: AsyncEngine,
) -> None:
    """E2E-046: > 4 KB tool I/O renders truncated and full endpoint streams whole JSON."""
    client = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="alice-agent")

    now = datetime.now(UTC)
    run_id = await _seed_run_full(
        db_engine,
        agent_id=agent_id,
        started_at=now,
        status="completed",
        final_answer="ok",
    )

    payload = _build_5kb_payload()
    step_id = await _seed_step(
        db_engine,
        run_id=run_id,
        seq=1,
        role="tool",
        tool_name="big",
        tool_result_json=payload,
    )

    response = await client.get(f"/agents/{agent_id}/runs/{run_id}")
    assert response.status_code == 200, response.text
    body = response.text

    # Truncated container present.
    assert 'class="truncated"' in body, body
    # View full button with the streamed-payload endpoint URL.
    expected_full = f"/agents/{agent_id}/runs/{run_id}/steps/{step_id}/full"
    assert f'hx-get="{expected_full}"' in body, body

    # Parse the result element and assert its rendered text length <= 4096.
    pattern = re.compile(
        rf'<div id="step-{step_id}-result"[^>]*class="truncated"[^>]*>'
        r'\s*<pre class="step-payload-text">(.*?)</pre>',
        re.DOTALL,
    )
    match = pattern.search(body)
    assert match is not None, body
    rendered = html.unescape(match.group(1))
    assert len(rendered) <= 4096, f"rendered length {len(rendered)} > 4096"

    # Full endpoint streams the FULL JSON payload (json.loads round-trip).
    full_response = await client.get(expected_full)
    assert full_response.status_code == 200
    parsed = json.loads(full_response.text)
    assert parsed == payload


async def test_e2e_047_dashboard_aggregation_matches_runs(
    login_as,
    fetch_user_id,
    seed_agent,
    db_engine: AsyncEngine,
) -> None:
    """E2E-047: dashboard tokens_in/out and cost equal the SUM over the seeded runs."""
    client = await login_as("alice@example.com", name="Alice")
    alice_id = await fetch_user_id("alice@example.com")
    agent_id = await seed_agent(alice_id, name="alice-agent")

    now = datetime.now(UTC)
    runs = [
        {"tokens_in": 10, "tokens_out": 20, "cost_usd": Decimal("0.001000")},
        {"tokens_in": 30, "tokens_out": 40, "cost_usd": Decimal("0.002000")},
        {"tokens_in": 50, "tokens_out": 60, "cost_usd": Decimal("0.003000")},
    ]
    expected_in = sum(r["tokens_in"] for r in runs)
    expected_out = sum(r["tokens_out"] for r in runs)
    expected_cost = sum((r["cost_usd"] for r in runs), Decimal("0"))

    for r in runs:
        await _seed_run_full(
            db_engine,
            agent_id=agent_id,
            started_at=now,
            status="completed",
            tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"],
            duration_ms=100,
            loops=1,
            cost_usd=r["cost_usd"],
            final_answer="ok",
        )

    response = await client.get("/agents")
    assert response.status_code == 200, response.text
    body = response.text

    in_match = re.search(r'class="agent-cell-tokens-in">\s*(\d+)', body)
    out_match = re.search(r'class="agent-cell-tokens-out">\s*(\d+)', body)
    cost_match = re.search(r'class="agent-cell-cost">\s*([^\s<]+)', body)

    assert in_match is not None and int(in_match.group(1)) == expected_in
    assert out_match is not None and int(out_match.group(1)) == expected_out
    assert cost_match is not None
    assert cost_match.group(1) == f"{expected_cost:.6f}"
