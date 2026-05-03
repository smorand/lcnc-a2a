"""US-005 acceptance tests for the Simple executor (E2E-026, 029, 048, 052, 055, 056, 058, 059)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    fetch_messages,
    fetch_runs_for_agent,
    install_llm_mock,
    make_a2a_envelope,
    post_a2a,
    seed_started_agent,
)


@pytest.mark.asyncio
async def test_e2e_048_simple_no_tools_end_to_end(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    stub = StubLlm()
    stub.add_text("Hello, world.", prompt_tokens=10, completion_tokens=5, cost=0.0001)
    install_llm_mock(respx_mock, stub)

    status, events, headers = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("Hi"),
    )
    assert status == 200, events
    assert headers["content-type"].startswith("text/event-stream")
    states = [e.get("state") for e in events if e.get("event") == "TaskStatusUpdate"]
    assert states[0] == "working"
    assert states[-1] == "completed"
    artifacts = [e for e in events if e.get("event") == "TaskArtifactUpdate"]
    assert artifacts, events
    rendered = "".join(part.get("text", "") for a in artifacts for part in a["artifact"]["parts"])
    assert rendered == "Hello, world."

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "completed"
    assert run["loops"] == 1
    assert run["tokens_in"] == 10
    assert run["tokens_out"] == 5
    assert run["final_answer"] == "Hello, world."

    async with db_engine.begin() as conn:
        context_id = (
            await conn.execute(
                text("SELECT id FROM agent_contexts WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()
    rows = await fetch_messages(db_engine, context_id)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "Hi"
    assert rows[1]["content"] == "Hello, world."


@pytest.mark.asyncio
async def test_e2e_026_post_to_stopped_returns_503(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE agents SET status = 'stopped' WHERE id = :id"),
            {"id": agent_id},
        )
    response = await http_client.post(
        f"/agents/{agent_id}",
        json=make_a2a_envelope("hi"),
        headers={"Authorization": f"Bearer {plain}"},
    )
    assert response.status_code == 503
    assert response.json() == {"error": "agent_stopped"}


@pytest.mark.asyncio
async def test_e2e_029_503_body_exact(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE agents SET status = 'stopped' WHERE id = :id"),
            {"id": agent_id},
        )
    response = await http_client.post(
        f"/agents/{agent_id}",
        json=make_a2a_envelope("hi"),
        headers={"Authorization": f"Bearer {plain}"},
    )
    assert response.status_code == 503
    assert response.json() == {"error": "agent_stopped"}


@pytest.mark.asyncio
async def test_e2e_052_llm_500_marks_run_failed(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    stub = StubLlm()
    stub.add_status(status=500, body="boom")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200
    assert events[-1] == {"event": "TaskStatusUpdate", "state": "failed", "reason": "llm_provider_error"}
    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["stop_reason"] == "llm_provider_error"


@pytest.mark.asyncio
async def test_e2e_055_context_id_reuses_prior_messages(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    context_id = uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO agent_contexts (id, agent_id, message_count) VALUES (:id, :agent_id, :count)"),
            {"id": context_id, "agent_id": agent_id, "count": 1},
        )
        await conn.execute(
            text(
                "INSERT INTO agent_messages (id, context_id, role, content, position) VALUES "
                "(:id, :context_id, 'assistant', 'Last answer was 5', 0)"
            ),
            {"id": uuid.uuid4(), "context_id": context_id},
        )
    stub = StubLlm()
    stub.add_text("Because.")
    install_llm_mock(respx_mock, stub)

    status, _events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("Why?", context_id=str(context_id)),
    )
    assert status == 200
    assert len(stub.calls) == 1
    msgs = stub.calls[0]["messages"]
    contents = [m.get("content") for m in msgs]
    assert "Last answer was 5" in contents
    assert "Why?" in contents
    assert contents.index("Last answer was 5") < contents.index("Why?")


@pytest.mark.asyncio
async def test_e2e_056_openrouter_cost_recorded(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    stub = StubLlm()
    stub.add_text("ok", prompt_tokens=100, completion_tokens=200, cost=0.0042)
    install_llm_mock(respx_mock, stub)

    status, _events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200
    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["tokens_in"] == 100
    assert runs[0]["tokens_out"] == 200
    assert runs[0]["cost_usd"] == Decimal("0.004200")


@pytest.mark.asyncio
async def test_e2e_058_soft_cap_drops_oldest_non_system(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    context_id = uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO agent_contexts (id, agent_id, message_count) VALUES (:id, :agent_id, :count)"),
            {"id": context_id, "agent_id": agent_id, "count": 61},
        )
        await conn.execute(
            text(
                "INSERT INTO agent_messages (id, context_id, role, content, position) VALUES "
                "(:id, :context_id, 'system', 'system base', 0)"
            ),
            {"id": uuid.uuid4(), "context_id": context_id},
        )
        rows = [
            {
                "id": uuid.uuid4(),
                "context_id": context_id,
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"hist-{i:03d}",
                "position": i,
            }
            for i in range(1, 61)
        ]
        await conn.execute(
            text(
                "INSERT INTO agent_messages (id, context_id, role, content, position) "
                "VALUES (:id, :context_id, :role, :content, :position)"
            ),
            rows,
        )
    stub = StubLlm()
    stub.add_text("bye")
    install_llm_mock(respx_mock, stub)

    status, _events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("Why?", context_id=str(context_id)),
    )
    assert status == 200
    assert len(stub.calls) == 1
    payload_msgs = stub.calls[0]["messages"]
    assert len(payload_msgs) <= 50
    contents = [m.get("content") for m in payload_msgs]
    assert "You are helpful." in contents  # system prompt always included
    assert "Why?" in contents
    # Oldest dropped: hist-001 must be missing (we only kept the newest non-system rows)
    assert "hist-001" not in contents

    async with db_engine.begin() as conn:
        post_count = (
            await conn.execute(
                text("SELECT count(*) FROM agent_messages WHERE context_id = :c"),
                {"c": context_id},
            )
        ).scalar_one()
    # Started at 61 (1 system + 60 history), added user + assistant.
    assert post_count >= 61


@pytest.mark.asyncio
async def test_e2e_059_hard_cap_emits_context_full(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    context_id = uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO agent_contexts (id, agent_id, message_count) VALUES (:id, :agent_id, :count)"),
            {"id": context_id, "agent_id": agent_id, "count": 1000},
        )
        rows = [
            {
                "id": uuid.uuid4(),
                "context_id": context_id,
                "role": "assistant",
                "content": f"msg-{i}",
                "position": i,
            }
            for i in range(1000)
        ]
        await conn.execute(
            text(
                "INSERT INTO agent_messages (id, context_id, role, content, position) "
                "VALUES (:id, :context_id, :role, :content, :position)"
            ),
            rows,
        )
    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("Why?", context_id=str(context_id)),
    )
    assert status == 200
    assert events[-1] == {"event": "TaskStatusUpdate", "state": "failed", "reason": "context_full"}

    async with db_engine.begin() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM agent_messages WHERE context_id = :c"),
                {"c": context_id},
            )
        ).scalar_one()
    assert count == 1000
