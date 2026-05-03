"""US-007 PE replan + token-budget acceptance tests (E2E-079, 080, 084)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    fetch_runs_for_agent,
    install_llm_mock,
    make_a2a_envelope,
    post_a2a,
)
from tests.e2e._pe_helpers import (
    add_planner_response,
    add_step_response,
    add_synthesis_response,
    plan_json,
    plan_step,
    seed_pe_mcp,
    seed_started_pe_agent,
)


def _planner_calls(stub: StubLlm) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for call in stub.calls:
        msgs = call.get("messages") or []
        if not msgs:
            continue
        first = msgs[0]
        if isinstance(first, dict) and "planner" in (first.get("content") or ""):
            out.append(call)
    return out


@pytest.mark.asyncio
async def test_e2e_079_pe_max_tokens_forces_synthesis(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id, max_tokens=300)
    search_touch = tmp_path / "search.log"
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["search"],
        env={"FAKE_MCP_PE_SEARCH_TOUCH": str(search_touch)},
    )

    plan_payload = plan_json(
        goal="x",
        steps=[plan_step(step_id=i, stage=i, tool="search") for i in range(1, 6)],
    )
    stub = StubLlm()
    add_planner_response(stub, plan_payload, prompt_tokens=0, completion_tokens=100, cost=0.0)
    for sid in range(1, 6):
        add_step_response(
            stub,
            step_id=sid,
            status="success",
            output=f"o{sid}",
            prompt_tokens=0,
            completion_tokens=100,
            cost=0.0,
        )
    add_synthesis_response(stub, text="forced", prompt_tokens=0, completion_tokens=10, cost=0.0)
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run = runs[0]
    assert run["status"] == "completed", run
    assert run["stop_reason"] == "max_tokens", run

    # Only steps 1 and 2 invoked the MCP tool (steps 3..5 skipped).
    assert search_touch.exists()
    call_lines = [line for line in search_touch.read_text().splitlines() if line]
    assert len(call_lines) == 2

    # Final SSE state is "completed".
    assert events[-1] == {"event": "TaskStatusUpdate", "state": "completed"}


@pytest.mark.asyncio
async def test_e2e_080_pe_replan_exceeded_after_three_replans(
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

    one_step_plan = plan_json(
        goal="g",
        steps=[plan_step(step_id=1, stage=1, tool="search")],
    )
    stub = StubLlm()
    # 4 plan + step pairs (initial + 3 replans + final attempt that exceeds the budget).
    for _ in range(4):
        add_planner_response(stub, one_step_plan)
        add_step_response(stub, step_id=1, status="replan_requested", reason="need_more_info")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events
    assert events[-1] == {"event": "TaskStatusUpdate", "state": "failed", "reason": "replan_exceeded"}

    planner_calls = _planner_calls(stub)
    assert len(planner_calls) == 4

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["stop_reason"] == "replan_exceeded"


@pytest.mark.asyncio
async def test_e2e_084_pe_replan_replaces_remaining_keeps_completed(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    search_touch = tmp_path / "search.log"
    market_touch = tmp_path / "market.log"
    ratios_touch = tmp_path / "ratios.log"
    echo_touch = tmp_path / "echo.log"
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["search", "get_market_data", "compute_ratios", "echo"],
        env={
            "FAKE_MCP_PE_SEARCH_TOUCH": str(search_touch),
            "FAKE_MCP_PE_MARKET_TOUCH": str(market_touch),
            "FAKE_MCP_PE_RATIOS_TOUCH": str(ratios_touch),
            "FAKE_MCP_PE_ECHO_TOUCH": str(echo_touch),
        },
    )

    initial_plan = plan_json(
        goal="g",
        steps=[
            plan_step(step_id=1, stage=1, tool="search"),
            plan_step(step_id=2, stage=2, tool="get_market_data", depends_on=[1]),
            plan_step(step_id=3, stage=3, tool="compute_ratios", depends_on=[2]),
            plan_step(step_id=4, stage=4, tool="echo", depends_on=[3]),
        ],
    )
    replan = plan_json(
        goal="g",
        steps=[
            plan_step(step_id=10, stage=3, tool="echo"),
            plan_step(step_id=11, stage=4, tool="echo", depends_on=[10]),
        ],
    )

    stub = StubLlm()
    add_planner_response(stub, initial_plan)
    add_step_response(stub, step_id=1, status="success", output="o1")
    add_step_response(stub, step_id=2, status="replan_requested", reason="need_more_info")
    add_planner_response(stub, replan)
    add_step_response(stub, step_id=10, status="success", output="o10")
    add_step_response(stub, step_id=11, status="success", output="o11")
    add_synthesis_response(stub, text="final")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events
    assert events[-1] == {"event": "TaskStatusUpdate", "state": "completed"}

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run_id = runs[0]["id"]

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT step_id, step_status FROM agent_run_steps WHERE run_id = :r "
                    "AND role = 'step_result' ORDER BY seq"
                ),
                {"r": run_id},
            )
        ).all()
    by_step = {r[0]: r[1] for r in rows}
    assert by_step[1] == "success"
    assert by_step[2] == "replan_requested"
    assert by_step[10] == "success"
    assert by_step[11] == "success"
    assert len(rows) == 4

    # Original step 3 (compute_ratios) and step 4 (echo via stage 4) were not run
    # via their original plan tools; ratios fixture must have zero invocations.
    assert not ratios_touch.exists() or ratios_touch.read_text() == ""
    # echo runs twice (steps 10, 11 in the replan).
    assert echo_touch.exists()
    echo_calls = [line for line in echo_touch.read_text().splitlines() if line]
    assert len(echo_calls) == 2
