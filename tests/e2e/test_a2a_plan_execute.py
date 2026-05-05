"""US-007 Plan & Execute happy-path acceptance tests (E2E-073, 074, 085)."""

from __future__ import annotations

import asyncio
import json
import time

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
async def test_e2e_073_pe_happy_path_three_sequential_steps(
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
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["search", "get_market_data", "compute_ratios"],
        env={
            "FAKE_MCP_PE_SEARCH_TOUCH": str(search_touch),
            "FAKE_MCP_PE_MARKET_TOUCH": str(market_touch),
            "FAKE_MCP_PE_RATIOS_TOUCH": str(ratios_touch),
        },
    )

    plan_payload = plan_json(
        goal="answer",
        steps=[
            plan_step(step_id=1, stage=1, tool="search", description="search bg", success_criterion="ok"),
            plan_step(
                step_id=2,
                stage=2,
                tool="get_market_data",
                description="market data",
                success_criterion="ok",
                depends_on=[1],
            ),
            plan_step(
                step_id=3,
                stage=3,
                tool="compute_ratios",
                description="ratios",
                success_criterion="ok",
                depends_on=[2],
            ),
        ],
    )

    stub = StubLlm()
    add_planner_response(stub, plan_payload)
    add_step_response(stub, step_id=1, status="success", output="o1")
    add_step_response(stub, step_id=2, status="success", output="o2")
    add_step_response(stub, step_id=3, status="success", output="o3")
    add_synthesis_response(stub, text="final")
    install_llm_mock(respx_mock, stub)

    status, events, headers = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200, events
    assert headers["content-type"].startswith("text/event-stream")

    phases = [event_phase(e) for e in events if is_status_event(e) and event_phase(e) is not None]
    assert phases == [
        "planning",
        "executing",
        "executing",
        "executing",
        "synthesizing",
    ]

    executing_events = [e for e in events if is_status_event(e) and event_phase(e) == "executing"]
    metas = [e["statusUpdate"]["metadata"] for e in executing_events]
    assert [m["stage"] for m in metas] == [1, 2, 3]
    assert [m["steps"] for m in metas] == [[1], [2], [3]]

    artifacts = [e for e in events if is_artifact_event(e)]
    rendered = "".join(artifact_text(a) for a in artifacts)
    assert rendered == "final"
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    assert search_touch.exists() and search_touch.read_text().strip().startswith("search:")
    assert market_touch.exists() and market_touch.read_text().strip().startswith("market:")
    assert ratios_touch.exists() and ratios_touch.read_text().strip().startswith("ratios:")

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"

    async with db_engine.begin() as conn:
        plan_value = (
            await conn.execute(text("SELECT plan FROM agent_runs WHERE id = :r"), {"r": runs[0]["id"]})
        ).scalar_one()
    assert plan_value == plan_payload

    steps = await fetch_steps(db_engine, runs[0]["id"])
    roles = [s["role"] for s in steps]
    assert roles == ["plan", "step_result", "step_result", "step_result", "synthesis"]


@pytest.mark.asyncio
async def test_e2e_074_pe_parallel_stage_executes_concurrently(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    slow_touch = tmp_path / "slow.log"
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["slow"],
        env={"FAKE_MCP_PE_SLOW_TOUCH": str(slow_touch)},
    )

    plan_payload = plan_json(
        goal="parallel",
        steps=[
            plan_step(step_id=1, stage=1, tool="slow"),
            plan_step(step_id=2, stage=1, tool="slow"),
            plan_step(step_id=3, stage=1, tool="slow"),
        ],
    )

    stub = StubLlm()
    add_planner_response(stub, plan_payload)
    add_step_response(stub, step_id=1, status="success", output="ok")
    add_step_response(stub, step_id=2, status="success", output="ok")
    add_step_response(stub, step_id=3, status="success", output="ok")
    add_synthesis_response(stub, text="final")
    install_llm_mock(respx_mock, stub)

    timings: dict[str, float] = {}
    async with http_client.stream(
        "POST",
        f"/agents/{agent_id}/message:stream",
        json=make_a2a_envelope("hi"),
        headers={"Authorization": f"Bearer {plain}"},
    ) as response:
        async for line in response.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = json.loads(line[5:].strip())
            update = data.get("statusUpdate") if isinstance(data, dict) else None
            metadata = update.get("metadata") if isinstance(update, dict) else None
            phase = metadata.get("phase") if isinstance(metadata, dict) else None
            now = time.perf_counter()
            if phase == "executing" and "executing_stage_1" not in timings:
                timings["executing_stage_1"] = now
            elif phase == "synthesizing":
                timings["synthesizing"] = now

    assert "executing_stage_1" in timings, timings
    assert "synthesizing" in timings, timings
    delta_ms = (timings["synthesizing"] - timings["executing_stage_1"]) * 1000
    assert delta_ms < 500, f"parallel stage took {delta_ms:.0f} ms (>=500ms suggests sequential)"
    assert slow_touch.exists()
    assert slow_touch.read_text().count("slow") == 3


@pytest.mark.asyncio
async def test_e2e_085_pe_state_transitions_running_to_completed(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
) -> None:
    """While the synthesis call is stalled the run row is ``running``; once it
    unblocks the row becomes ``completed`` with ``completed_at`` populated."""
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_pe_agent(db_engine, user_id=user_id)
    search_touch = tmp_path / "search.log"
    await seed_pe_mcp(
        db_engine,
        agent_id=agent_id,
        tool_names=["search"],
        env={"FAKE_MCP_PE_SEARCH_TOUCH": str(search_touch)},
    )

    plan_payload = plan_json(
        goal="g",
        steps=[plan_step(step_id=1, stage=1, tool="search")],
    )

    synthesis_unblock = asyncio.Event()
    pre_synthesis_status: dict[str, str | None] = {}

    async def _on_call(idx: int, payload: dict) -> None:
        # First call = planner; second = executor; third = synthesis.
        if idx == 2:
            # Capture the run-row state before the synthesis returns.
            async with db_engine.begin() as conn:
                row = (
                    await conn.execute(
                        text("SELECT status, completed_at FROM agent_runs WHERE agent_id = :a"),
                        {"a": agent_id},
                    )
                ).one()
            pre_synthesis_status["status"] = str(row[0])
            pre_synthesis_status["completed_at"] = row[1]
            synthesis_unblock.set()

    stub = StubLlm(on_call=_on_call)
    add_planner_response(stub, plan_payload)
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
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    assert pre_synthesis_status["status"] == "running"
    assert pre_synthesis_status["completed_at"] is None

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "completed"
    async with db_engine.begin() as conn:
        completed_at = (
            await conn.execute(text("SELECT completed_at FROM agent_runs WHERE id = :r"), {"r": runs[0]["id"]})
        ).scalar_one()
    assert completed_at is not None
