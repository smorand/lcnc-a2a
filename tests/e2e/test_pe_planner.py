"""US-007 PE planner validation acceptance tests (E2E-075..078)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    event_reason,
    event_state,
    fetch_runs_for_agent,
    install_llm_mock,
    make_a2a_envelope,
    post_a2a,
)
from tests.e2e._pe_helpers import (
    add_planner_response,
    add_raw_text,
    add_step_response,
    add_synthesis_response,
    plan_json,
    plan_step,
    seed_pe_mcp,
    seed_started_pe_agent,
)


def _planner_calls(stub: StubLlm) -> list[dict[str, Any]]:
    """Return only the calls whose first system message is the planner prompt."""
    out: list[dict[str, Any]] = []
    for call in stub.calls:
        msgs = call.get("messages") or []
        if not msgs:
            continue
        first = msgs[0]
        if isinstance(first, dict) and "planner" in (first.get("content") or ""):
            out.append(call)
    return out


def _flatten_messages(call: dict[str, Any]) -> str:
    return "\n".join((m.get("content") or "") for m in (call.get("messages") or []) if isinstance(m, dict))


@pytest.mark.asyncio
async def test_e2e_075_planner_returns_invalid_json_twice_planning_failed(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    await seed_pe_mcp(db_engine, agent_id=agent_id, tool_names=["search"])

    stub = StubLlm()
    add_raw_text(stub, content="not json")
    add_raw_text(stub, content="not json")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events

    planner_calls = _planner_calls(stub)
    assert len(planner_calls) == 2

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["stop_reason"] == "planning_failed"
    assert event_state(events[-1]) == "TASK_STATE_FAILED" and event_reason(events[-1]) == "planning_failed"


@pytest.mark.asyncio
async def test_e2e_076_planner_exceeds_max_steps_retries_then_fails(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id, max_steps=3)
    await seed_pe_mcp(db_engine, agent_id=agent_id, tool_names=["search"])

    too_many = plan_json(
        goal="x",
        steps=[plan_step(step_id=i, stage=i, tool="search") for i in range(1, 6)],
    )
    stub = StubLlm()
    add_planner_response(stub, too_many)
    add_planner_response(stub, too_many)
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events

    planner_calls = _planner_calls(stub)
    assert len(planner_calls) == 2
    second_blob = _flatten_messages(planner_calls[1])
    assert "max_steps=3" in second_blob, second_blob

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["stop_reason"] == "planning_failed"


@pytest.mark.asyncio
async def test_e2e_077_unknown_tool_rejected_then_retry_succeeds(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["search"],
        env={"FAKE_MCP_PE_SEARCH_TOUCH": str(tmp_path / "search.log")},
    )

    bad_plan = plan_json(
        goal="g",
        steps=[plan_step(step_id=1, stage=1, tool="nonexistent")],
    )
    good_plan = plan_json(
        goal="g",
        steps=[plan_step(step_id=1, stage=1, tool="search")],
    )

    stub = StubLlm()
    add_planner_response(stub, bad_plan)
    add_planner_response(stub, good_plan)
    add_step_response(stub, step_id=1, status="success", output="ok")
    add_synthesis_response(stub, text="final")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events

    planner_calls = _planner_calls(stub)
    assert len(planner_calls) == 2
    second_blob = _flatten_messages(planner_calls[1])
    assert "unknown tool: nonexistent" in second_blob, second_blob

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "completed"


@pytest.mark.asyncio
async def test_e2e_078_forward_dependency_rejected_then_retry_succeeds(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["search"],
        env={"FAKE_MCP_PE_SEARCH_TOUCH": str(tmp_path / "search.log")},
    )

    bad_plan = plan_json(
        goal="g",
        steps=[
            plan_step(step_id=1, stage=1, tool="search", depends_on=[2]),
            plan_step(step_id=2, stage=2, tool="search"),
        ],
    )
    good_plan = plan_json(
        goal="g",
        steps=[plan_step(step_id=1, stage=1, tool="search")],
    )

    stub = StubLlm()
    add_planner_response(stub, bad_plan)
    add_planner_response(stub, good_plan)
    add_step_response(stub, step_id=1, status="success", output="ok")
    add_synthesis_response(stub, text="final")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events

    planner_calls = _planner_calls(stub)
    assert len(planner_calls) >= 2
    second_blob = _flatten_messages(planner_calls[1])
    assert "forward dependency" in second_blob, second_blob

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "completed"


# Used internally to keep _flatten_messages from getting flagged as unused.
_ = json
