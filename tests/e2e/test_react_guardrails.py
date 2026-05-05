"""US-006 acceptance tests for ReAct guardrails (E2E-062, 063, 064, 067)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    event_reason,
    event_state,
    fetch_runs_for_agent,
    fetch_steps,
    install_llm_mock,
    make_a2a_envelope,
    post_a2a,
)
from tests.e2e._react_helpers import (
    StubEmbedding,
    add_react_tool_call,
    add_unparseable,
    install_embedding_mock,
    make_embedding,
    seed_started_react_agent,
)
from tests.e2e.test_a2a_react import _seed_mcp_with_noop

FAKE_MCP_ADD = "python -m tests.e2e.fixtures.fake_mcp_add"

NOOP_TOOL = {
    "name": "noop",
    "description": "No-op tool.",
    "input_schema": {"type": "object", "properties": {}},
}


@pytest.mark.asyncio
async def test_e2e_062_react_max_loops_forces_synthesis(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=3, max_tokens=8000, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    add_react_tool_call(stub, thought="t1", tool_name="noop", arguments={})
    add_react_tool_call(stub, thought="t2", tool_name="noop", arguments={})
    add_react_tool_call(stub, thought="t3", tool_name="noop", arguments={})
    stub.add_text("synthesized answer", prompt_tokens=1, completion_tokens=2, cost=0.0)
    install_llm_mock(respx_mock, stub)

    # Dissimilar embeddings to avoid early stop.
    embed_stub = StubEmbedding()
    for i in range(5):
        embed_stub.add_vector(make_embedding(seed=10 + i))
    install_embedding_mock(respx_mock, embed_stub)

    status, _events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run = runs[0]
    assert run["loops"] == 3
    assert run["stop_reason"] == "max_loops"
    assert run["status"] == "completed"
    assert run["final_answer"] == "synthesized answer"

    # Exactly 4 LLM chat calls (3 iters + 1 synthesis).
    assert len(stub.calls) == 4


@pytest.mark.asyncio
async def test_e2e_063_react_max_tokens_forces_synthesis(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=50, max_tokens=200, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    add_react_tool_call(stub, thought="t", tool_name="noop", arguments={}, prompt_tokens=0, completion_tokens=100)
    add_react_tool_call(stub, thought="t", tool_name="noop", arguments={}, prompt_tokens=0, completion_tokens=100)
    stub.add_text("synth final", prompt_tokens=1, completion_tokens=10, cost=0.0)
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    for i in range(4):
        embed_stub.add_vector(make_embedding(seed=20 + i))
    install_embedding_mock(respx_mock, embed_stub)

    status, _events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run = runs[0]
    assert run["stop_reason"] == "max_tokens"
    assert run["status"] == "completed"
    assert run["final_answer"] == "synth final"


@pytest.mark.asyncio
async def test_e2e_064_react_synthesis_overshoot_skipped(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=2, max_tokens=200, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    long_thought = "x" * 1200  # scratchpad chars/4 → ~ 600 estimated tokens, > max*0.5

    stub = StubLlm()
    add_react_tool_call(stub, thought=long_thought, tool_name="noop", arguments={})
    add_react_tool_call(stub, thought=long_thought, tool_name="noop", arguments={})
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    for i in range(4):
        embed_stub.add_vector(make_embedding(seed=30 + i))
    install_embedding_mock(respx_mock, embed_stub)

    status, events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200, events

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run = runs[0]
    assert run["status"] == "failed"
    assert run["stop_reason"] == "guardrail_exceeded_no_synthesis"

    # Exactly 2 LLM chat calls (no synthesis call).
    assert len(stub.calls) == 2

    last = events[-1]
    assert event_state(last) == "TASK_STATE_FAILED"
    assert event_reason(last) == "guardrail_exceeded_no_synthesis"


@pytest.mark.asyncio
async def test_e2e_067_react_unparseable_counts_as_loop(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=2, max_tokens=8000, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    stub = StubLlm()
    add_unparseable(stub, content="garbled output without structure")
    # Iter 2 returns a final answer.
    stub.responses.append(
        {
            "id": "resp-final",
            "choices": [{"message": {"role": "assistant", "content": "Final Answer: ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0001},
        }
    )
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    install_embedding_mock(respx_mock, embed_stub)

    status, _events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run = runs[0]
    assert run["loops"] == 2
    assert run["status"] == "completed"

    steps = await fetch_steps(db_engine, run["id"])
    error_steps = [s for s in steps if s["role"] == "error"]
    assert error_steps, steps
    assert "parse_error" in (error_steps[0]["content"] or "")
