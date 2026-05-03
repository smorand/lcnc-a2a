"""US-007 PE single-step + substitution + persistence tests (E2E-081, 082, 083)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    fetch_runs_for_agent,
    fetch_steps,
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


@pytest.mark.asyncio
async def test_e2e_081_pe_single_step_synthesize_tool(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    await seed_pe_mcp(db_engine, agent_id=agent_id, tool_names=["search"])

    plan_payload = plan_json(
        goal="g",
        steps=[plan_step(step_id=1, stage=1, tool="synthesize", description="just synthesize")],
    )
    stub = StubLlm()
    add_planner_response(stub, plan_payload)
    add_step_response(stub, step_id=1, status="success", output="x")
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
    assert runs[0]["status"] == "completed"
    steps = await fetch_steps(db_engine, runs[0]["id"])
    roles = [s["role"] for s in steps]
    assert roles.count("plan") == 1
    assert roles.count("step_result") == 1
    assert roles.count("synthesis") == 1


@pytest.mark.asyncio
async def test_e2e_082_pe_substitutes_step_output_in_args(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    echo_touch = tmp_path / "echo.log"
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["echo"],
        env={"FAKE_MCP_PE_ECHO_TOUCH": str(echo_touch)},
    )

    plan_payload = plan_json(
        goal="g",
        steps=[
            plan_step(step_id=1, stage=1, tool="echo", args={"value": "42"}),
            plan_step(
                step_id=2,
                stage=2,
                tool="echo",
                args={"value": "${step_1.output}"},
                depends_on=[1],
            ),
        ],
    )
    stub = StubLlm()
    add_planner_response(stub, plan_payload)
    add_step_response(stub, step_id=1, status="success", output="42")
    add_step_response(stub, step_id=2, status="success", output="42")
    add_synthesis_response(stub, text="final")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events

    assert echo_touch.exists()
    raw = echo_touch.read_text()
    # The fake fixture writes "echo:<value>" per call; both calls received "42".
    lines = [line for line in raw.splitlines() if line]
    assert lines == ["echo:42", "echo:42"], raw


@pytest.mark.asyncio
async def test_e2e_083_pe_persists_plan_and_step_results(
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
        tool_names=["search", "get_market_data", "compute_ratios"],
        env={
            "FAKE_MCP_PE_SEARCH_TOUCH": str(tmp_path / "s.log"),
            "FAKE_MCP_PE_MARKET_TOUCH": str(tmp_path / "m.log"),
            "FAKE_MCP_PE_RATIOS_TOUCH": str(tmp_path / "r.log"),
        },
    )

    plan_payload = plan_json(
        goal="g",
        steps=[
            plan_step(step_id=1, stage=1, tool="search"),
            plan_step(step_id=2, stage=2, tool="get_market_data", depends_on=[1]),
            plan_step(step_id=3, stage=3, tool="compute_ratios", depends_on=[2]),
        ],
    )
    stub = StubLlm()
    add_planner_response(stub, plan_payload)
    for sid in (1, 2, 3):
        add_step_response(stub, step_id=sid, status="success", output=f"o{sid}")
    add_synthesis_response(stub, text="final")
    install_llm_mock(respx_mock, stub)

    status, _events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run_id = runs[0]["id"]
    async with db_engine.begin() as conn:
        plan_value = (await conn.execute(text("SELECT plan FROM agent_runs WHERE id = :r"), {"r": run_id})).scalar_one()
        rows = (
            await conn.execute(
                text(
                    "SELECT step_id, stage, step_status FROM agent_run_steps "
                    "WHERE run_id = :r AND role = 'step_result' ORDER BY seq"
                ),
                {"r": run_id},
            )
        ).all()
    assert plan_value == plan_payload
    assert len(rows) == 3
    for row in rows:
        assert row[0] is not None
        assert row[1] is not None
        assert row[2] is not None
