"""US-005 lifecycle interactions (E2E-028, 033, 090, 091, 092, 093)."""

from __future__ import annotations

import asyncio
import re
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    event_state,
    fetch_runs_for_agent,
    install_llm_mock,
    is_status_event,
    make_a2a_envelope,
    post_a2a,
    seed_started_agent,
)


async def _ui_csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def _csrf_for_agent_edit(client: httpx.AsyncClient, agent_id: uuid.UUID) -> str:
    response = await client.get(f"/agents/{agent_id}/edit")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_e2e_028_stop_does_not_interrupt_in_flight_run(
    seed_user,
    login_as,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    release = asyncio.Event()
    stop_signal = asyncio.Event()

    async def on_call(_index: int, _payload: object) -> None:
        stop_signal.set()
        await release.wait()

    stub = StubLlm(on_call=on_call)
    stub.add_text("done")
    install_llm_mock(respx_mock, stub)

    client = await login_as("alice@example.com", name="Alice")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    a2a_task = asyncio.create_task(
        post_a2a(
            http_client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope("hi"),
        )
    )
    await asyncio.wait_for(stop_signal.wait(), timeout=5.0)

    stop_response = await client.post(
        f"/agents/{agent_id}/stop",
        data={"csrf_token": csrf},
    )
    assert stop_response.status_code == 302
    release.set()

    status, events, _ = await asyncio.wait_for(a2a_task, timeout=5.0)
    assert status == 200
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "completed"

    blocked = await http_client.post(
        f"/agents/{agent_id}/message:stream",
        json=make_a2a_envelope("hi again"),
        headers={"Authorization": f"Bearer {plain}"},
    )
    assert blocked.status_code == 503


@pytest.mark.asyncio
async def test_e2e_033_delete_cancels_in_flight_run(
    seed_user,
    login_as,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    release = asyncio.Event()
    stalled = asyncio.Event()

    async def on_call(_index: int, _payload: object) -> None:
        stalled.set()
        await release.wait()

    stub = StubLlm(on_call=on_call)
    stub.add_text("done")
    install_llm_mock(respx_mock, stub)

    client = await login_as("alice@example.com", name="Alice")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    a2a_task = asyncio.create_task(
        post_a2a(
            http_client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope("hi"),
        )
    )
    await asyncio.wait_for(stalled.wait(), timeout=5.0)

    delete = await client.post(
        f"/agents/{agent_id}",
        data={"_method": "DELETE", "csrf_token": csrf},
    )
    assert delete.status_code == 302
    release.set()

    status, events, _ = await asyncio.wait_for(a2a_task, timeout=5.0)
    assert status == 200
    assert is_status_event(events[-1])
    assert event_state(events[-1]) == "TASK_STATE_CANCELED"

    async with db_engine.begin() as conn:
        count = (
            await conn.execute(
                text("SELECT count(*) FROM agent_runs WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_e2e_091_delete_emits_cancelled_envelope(
    seed_user,
    login_as,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    release = asyncio.Event()
    stalled = asyncio.Event()

    async def on_call(_index: int, _payload: object) -> None:
        stalled.set()
        await release.wait()

    stub = StubLlm(on_call=on_call)
    stub.add_text("done")
    install_llm_mock(respx_mock, stub)

    client = await login_as("alice@example.com", name="Alice")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    a2a_task = asyncio.create_task(
        post_a2a(
            http_client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope("hi"),
        )
    )
    await asyncio.wait_for(stalled.wait(), timeout=5.0)
    await client.post(
        f"/agents/{agent_id}",
        data={"_method": "DELETE", "csrf_token": csrf},
    )
    release.set()

    _status, events, _ = await asyncio.wait_for(a2a_task, timeout=5.0)
    assert event_state(events[-1]) == "TASK_STATE_CANCELED"


@pytest.mark.asyncio
async def test_e2e_092_stop_mid_run_lets_finish(
    seed_user,
    login_as,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    """Same shape as E2E-028; alias for security suite traceability."""
    await test_e2e_028_stop_does_not_interrupt_in_flight_run(seed_user, login_as, db_engine, http_client, respx_mock)


@pytest.mark.asyncio
async def test_e2e_090_edit_mid_run_does_not_affect_in_flight(
    seed_user,
    login_as,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    # Set system_prompt = "OLD"
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    async with db_engine.begin() as conn:
        await conn.execute(
            text("UPDATE agents SET system_prompt = 'OLD' WHERE id = :id"),
            {"id": agent_id},
        )

    release = asyncio.Event()
    stalled = asyncio.Event()

    async def on_call(_index: int, _payload: object) -> None:
        stalled.set()
        await release.wait()

    stub = StubLlm(on_call=on_call)
    stub.add_text("done", cost=None)
    install_llm_mock(respx_mock, stub)

    client = await login_as("alice@example.com", name="Alice")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    a2a_task = asyncio.create_task(
        post_a2a(
            http_client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope("hi"),
        )
    )
    await asyncio.wait_for(stalled.wait(), timeout=5.0)

    edit = await client.post(
        f"/agents/{agent_id}",
        data={
            "name": "agent-A",
            "description": "",
            "mode": "simple",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.example.com/api/v1",
            "model_id": "mock-model",
            "system_prompt": "NEW",
            "max_loops": "10",
            "max_tokens": "8000",
            "csrf_token": csrf,
        },
    )
    assert edit.status_code in (200, 302)
    release.set()
    await asyncio.wait_for(a2a_task, timeout=5.0)

    captured = stub.calls[0]["messages"]
    assert captured[0]["role"] == "system"
    assert captured[0]["content"] == "OLD"

    # New run captures the new snapshot.
    stub2 = StubLlm()
    stub2.add_text("again", cost=None)
    respx_mock.routes.clear()
    install_llm_mock(respx_mock, stub2)

    status, _events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("again"),
    )
    assert status == 200
    runs = await fetch_runs_for_agent(db_engine, agent_id)
    fresh = runs[-1]
    assert fresh["config_snapshot"]["system_prompt"] == "NEW"


@pytest.mark.asyncio
async def test_e2e_093_concurrent_contexts_isolation(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    c1 = uuid.uuid4()
    c2 = uuid.uuid4()
    async with db_engine.begin() as conn:
        for cid, content in [(c1, "answer-from-1"), (c2, "answer-from-2")]:
            await conn.execute(
                text("INSERT INTO agent_contexts (id, agent_id, message_count) VALUES (:id, :agent_id, :count)"),
                {"id": cid, "agent_id": agent_id, "count": 1},
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_messages (id, context_id, role, content, position) VALUES "
                    "(:id, :context_id, 'assistant', :content, 0)"
                ),
                {"id": uuid.uuid4(), "context_id": cid, "content": content},
            )

    stub = StubLlm(repeat_last=True)
    stub.add_text("ok")
    install_llm_mock(respx_mock, stub)

    results = await asyncio.gather(
        post_a2a(
            http_client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope("from-1", context_id=str(c1)),
        ),
        post_a2a(
            http_client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope("from-2", context_id=str(c2)),
        ),
    )
    for status, events, _ in results:
        assert status == 200
        assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    # Each captured request should contain only its own context's prior message.
    captured_contents = [[m.get("content") for m in call["messages"]] for call in stub.calls]
    seen_pairs = sorted(
        ("answer-from-1" in c, "answer-from-2" in c, "from-1" in c, "from-2" in c) for c in captured_contents
    )
    # Two captures expected; one with answer-from-1 + from-1, the other with answer-from-2 + from-2.
    assert seen_pairs == sorted([(True, False, True, False), (False, True, False, True)])

    async with db_engine.begin() as conn:
        c1_msgs = (
            await conn.execute(
                text("SELECT content FROM agent_messages WHERE context_id = :c ORDER BY position"),
                {"c": c1},
            )
        ).all()
        c2_msgs = (
            await conn.execute(
                text("SELECT content FROM agent_messages WHERE context_id = :c ORDER BY position"),
                {"c": c2},
            )
        ).all()
    c1_contents = [r.content for r in c1_msgs]
    c2_contents = [r.content for r in c2_msgs]
    assert "from-1" in c1_contents and "from-2" not in c1_contents
    assert "from-2" in c2_contents and "from-1" not in c2_contents
