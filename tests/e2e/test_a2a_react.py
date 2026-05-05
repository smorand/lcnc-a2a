"""US-006 acceptance tests for the ReAct executor (E2E-060, 061, 066, 068, 069, 070, 072)."""

from __future__ import annotations

import json
import uuid as _uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    artifact_text,
    event_phase,
    event_state,
    fetch_runs_for_agent,
    fetch_steps,
    install_llm_mock,
    is_artifact_event,
    is_status_event,
    make_a2a_envelope,
    post_a2a,
)
from tests.e2e._react_helpers import (
    StubEmbedding,
    add_final_answer,
    add_react_tool_call,
    encrypt_env,
    install_embedding_mock,
    make_embedding,
    seed_started_react_agent,
)

FAKE_MCP_ADD = "python -m tests.e2e.fixtures.fake_mcp_add"

NOOP_TOOL = {
    "name": "noop",
    "description": "No-op tool.",
    "input_schema": {"type": "object", "properties": {}},
}

FLAKY_TOOL = {
    "name": "flaky",
    "description": "Always errors.",
    "input_schema": {"type": "object", "properties": {}},
}


async def _seed_mcp_with_noop(db_engine: AsyncEngine, agent_id: _uuid.UUID, touch_file: str) -> _uuid.UUID:
    server_id = _uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, command, "
                "tools_cache, env_enc) VALUES (:id, :agent_id, 'stdio', :command, "
                ":cache, :env_enc)"
            ),
            {
                "id": server_id,
                "agent_id": agent_id,
                "command": FAKE_MCP_ADD,
                "cache": json.dumps({"tools": [NOOP_TOOL]}),
                "env_enc": encrypt_env({"FAKE_MCP_NOOP_TOUCH_FILE": touch_file}),
            },
        )
    return server_id


async def _seed_mcp_with_flaky(db_engine: AsyncEngine, agent_id: _uuid.UUID, touch_file: str) -> _uuid.UUID:
    server_id = _uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, command, "
                "tools_cache, env_enc) VALUES (:id, :agent_id, 'stdio', :command, "
                ":cache, :env_enc)"
            ),
            {
                "id": server_id,
                "agent_id": agent_id,
                "command": FAKE_MCP_ADD,
                "cache": json.dumps({"tools": [FLAKY_TOOL]}),
                "env_enc": encrypt_env({"FAKE_MCP_FLAKY_TOUCH_FILE": touch_file}),
            },
        )
    return server_id


@pytest.mark.asyncio
async def test_e2e_060_react_happy_path_stops_by_final(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=10, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    add_react_tool_call(stub, thought="thinking step", tool_name="noop", arguments={})
    add_final_answer(stub, text="final")
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    install_embedding_mock(respx_mock, embed_stub)

    status, events, headers = await post_a2a(
        http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi")
    )
    assert status == 200, events
    assert headers["content-type"].startswith("text/event-stream")

    phase_events = [event_phase(e) for e in events if is_status_event(e) and event_phase(e) is not None]
    assert phase_events == ["thought", "action", "observation"]

    artifacts = [e for e in events if is_artifact_event(e)]
    rendered = "".join(artifact_text(a) for a in artifacts)
    assert rendered == "final"
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "completed"
    assert runs[0]["loops"] == 2
    assert runs[0]["stop_reason"] == "final"
    assert runs[0]["final_answer"] == "final"


@pytest.mark.asyncio
async def test_e2e_061_react_stops_by_similarity_at_iter_3(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=10, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    add_react_tool_call(stub, thought="iter1 thought", tool_name="noop", arguments={})
    add_react_tool_call(stub, thought="iter2 thought", tool_name="noop", arguments={})
    add_react_tool_call(stub, thought="iter3 thought", tool_name="noop", arguments={})
    install_llm_mock(respx_mock, stub)

    # Embed sequence at iter 2: [iter1_text, iter2_text]; at iter 3: [iter3_text].
    # iter1 vec different from iter2 vec; iter3 vec equal to iter2 vec → cosine 1.0.
    iter2_vec = make_embedding(seed=200)
    embed_stub = StubEmbedding()
    embed_stub.add_vector(make_embedding(seed=100))  # iter 1 text
    embed_stub.add_vector(iter2_vec)  # iter 2 text
    embed_stub.add_vector(iter2_vec)  # iter 3 text (same as iter 2)
    install_embedding_mock(respx_mock, embed_stub)

    status, events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200, events
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run = runs[0]
    assert run["loops"] == 3
    assert run["stop_reason"] == "similarity"
    assert run["status"] == "completed"
    assert run["final_answer"] == "iter2 thought"

    steps = await fetch_steps(db_engine, run["id"])
    # Confirm at least one row has similarity_to_prev >= 0.95.
    async with db_engine.begin() as conn:
        sims = (
            await conn.execute(
                text("SELECT similarity_to_prev FROM agent_run_steps WHERE run_id = :r"),
                {"r": run["id"]},
            )
        ).all()
    sim_values = [row[0] for row in sims if row[0] is not None]
    assert any(s >= 0.95 for s in sim_values), sim_values
    assert len(steps) >= 6  # at least iter1 thought+action+obs + iter2 thought+action+obs


@pytest.mark.asyncio
async def test_e2e_066_react_tool_fails_3x_then_continues(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lcnc_a2a.executors import base as exec_base

    monkeypatch.setattr(exec_base, "TOOL_RETRY_BACKOFFS", (0.01, 0.01, 0.01))

    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(db_engine, user_id=user_id)
    touch_file = tmp_path / "flaky.log"
    await _seed_mcp_with_flaky(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    add_react_tool_call(stub, thought="will try flaky", tool_name="flaky", arguments={})
    add_final_answer(stub, text="recovered")
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    install_embedding_mock(respx_mock, embed_stub)

    status, events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("go"))
    assert status == 200, events
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    assert touch_file.exists()
    assert touch_file.read_text().count("call") == 3

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "completed"
    assert runs[0]["loops"] == 2

    steps = await fetch_steps(db_engine, runs[0]["id"])
    obs = next(s for s in steps if s["role"] == "observation")
    assert obs["tool_result_json"]["is_error"] is True
    assert "flaky" in str(obs["tool_result_json"]["content"]).lower() or obs["content"]


@pytest.mark.asyncio
async def test_e2e_068_react_threshold_099_does_not_stop_at_097(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(db_engine, user_id=user_id, max_loops=5, similarity_threshold=0.99)
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    for i in range(5):
        add_react_tool_call(stub, thought=f"thought {i}", tool_name="noop", arguments={})
    # Synthesis call (within budget).
    stub.add_text("synthesis ok", prompt_tokens=1, completion_tokens=1, cost=0.0)
    install_llm_mock(respx_mock, stub)

    # Embed sequence: iter2 sim check (iter1 text + iter2 text) → 0.97. Then
    # iter3,4,5 each embed once → all similarity ≈ 0.97. Use one repeating
    # vector pair that yields a fixed cosine.
    a = [1.0, 0.0]
    b = [0.97, _square_root(1 - 0.97**2)]  # cos(a,b) = 0.97
    embed_stub = StubEmbedding(repeat_last=True)
    embed_stub.add_vector(a)  # iter 1 text
    embed_stub.add_vector(b)  # iter 2 text  → cos(b,a) = 0.97
    embed_stub.add_vector(a)  # iter 3 text  → cos(a,b) = 0.97
    embed_stub.add_vector(b)  # iter 4 text  → cos(b,a) = 0.97
    embed_stub.add_vector(a)  # iter 5 text  → cos(a,b) = 0.97
    install_embedding_mock(respx_mock, embed_stub)

    status, events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200, events

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["stop_reason"] == "max_loops", runs[0]


def _square_root(x: float) -> float:
    return x**0.5


@pytest.mark.asyncio
async def test_e2e_069_react_threshold_inclusive_at_095(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=10, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    for _ in range(3):
        add_react_tool_call(stub, thought="iter thought", tool_name="noop", arguments={})
    install_llm_mock(respx_mock, stub)

    # cos(a,b) = 0.95 exactly between iter 2 and iter 3.
    a = [1.0, 0.0]
    b = [0.95, _square_root(1 - 0.95**2)]
    embed_stub = StubEmbedding()
    embed_stub.add_vector(make_embedding(seed=42))  # iter 1
    embed_stub.add_vector(a)  # iter 2  (vs iter 1 → some other cos < 0.95)
    embed_stub.add_vector(b)  # iter 3  (vs iter 2 = a) → cos = 0.95 exactly
    install_embedding_mock(respx_mock, embed_stub)

    status, _events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["stop_reason"] == "similarity"


@pytest.mark.asyncio
async def test_e2e_070_per_loop_trace_persisted(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=10, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    add_react_tool_call(stub, thought="t1", tool_name="noop", arguments={})
    add_react_tool_call(stub, thought="t2", tool_name="noop", arguments={})
    add_final_answer(stub, text="done")
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    embed_stub.add_vector(make_embedding(seed=1))
    embed_stub.add_vector(make_embedding(seed=2))
    install_embedding_mock(respx_mock, embed_stub)

    status, _events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run_id = runs[0]["id"]
    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT seq, role, tokens_in, tokens_out, similarity_to_prev "
                    "FROM agent_run_steps WHERE run_id = :r ORDER BY seq"
                ),
                {"r": run_id},
            )
        ).all()
    roles = [r[1] for r in rows]
    assert "thought" in roles and "action" in roles and "observation" in roles
    # iter 2's representative thought row carries similarity_to_prev.
    sim_set = [r for r in rows if r[1] == "thought" and r[4] is not None]
    assert sim_set, rows
    for row in sim_set:
        assert row[2] is not None  # tokens_in
        assert row[3] is not None  # tokens_out


@pytest.mark.asyncio
async def test_e2e_072_react_loops_equals_iterations_executed(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=10, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    for i in range(4):
        add_react_tool_call(stub, thought=f"iter {i}", tool_name="noop", arguments={})
    add_final_answer(stub, text="end")
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    for i in range(5):
        embed_stub.add_vector(make_embedding(seed=i + 1))
    install_embedding_mock(respx_mock, embed_stub)

    status, _events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["loops"] == 5
