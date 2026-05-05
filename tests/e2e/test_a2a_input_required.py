"""End-to-end tests for the TASK_STATE_INPUT_REQUIRED pause/resume flow.

A tool descriptor with ``annotations.destructiveHint=true`` triggers a
pause: the agent persists the pending tool call, emits an
``INPUT_REQUIRED`` status update, and ends the SSE stream. The client
resumes by re-sending a message with the same ``taskId`` carrying
``yes`` (approve) or anything else (deny).
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    artifact_text,
    event_phase,
    event_reason,
    event_state,
    fetch_messages,
    fetch_runs_for_agent,
    install_llm_mock,
    is_artifact_event,
    is_status_event,
    make_a2a_envelope,
    post_a2a,
    seed_started_agent,
)

DELETE_TOOL = {
    "name": "delete_file",
    "description": "Delete a file from disk.",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    "annotations": {"destructiveHint": True, "readOnlyHint": False},
}

SAFE_TOOL = {
    "name": "search",
    "description": "Search.",
    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
}


async def _seed_mcp(db_engine: AsyncEngine, *, agent_id: uuid.UUID, tools: list[dict]) -> uuid.UUID:
    server_id = uuid.uuid4()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, command, "
                "tools_cache) VALUES (:id, :agent_id, 'stdio', '/bin/true', :cache)"
            ),
            {"id": server_id, "agent_id": agent_id, "cache": json.dumps({"tools": tools})},
        )
    return server_id


@pytest.mark.asyncio
async def test_destructive_tool_pauses_with_input_required(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    """LLM proposes a destructive tool call → agent emits INPUT_REQUIRED, run = paused."""
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    await _seed_mcp(db_engine, agent_id=agent_id, tools=[DELETE_TOOL])

    stub = StubLlm()
    stub.add_tool_call(tool_name="delete_file", arguments={"path": "/tmp/x"})
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("delete /tmp/x"),
    )
    assert status == 200, events

    # Last status update must be INPUT_REQUIRED, not COMPLETED/FAILED.
    states = [event_state(e) for e in events if is_status_event(e)]
    assert "TASK_STATE_INPUT_REQUIRED" in states, states
    last_status = next(e for e in reversed(events) if is_status_event(e))
    assert event_state(last_status) == "TASK_STATE_INPUT_REQUIRED"
    update = last_status["statusUpdate"]
    assert update["final"] is False  # interrupted, not terminal
    # The status carries the prompt as a ROLE_AGENT message.
    msg = update["status"]["message"]
    assert msg["role"] == "ROLE_AGENT"
    assert "delete_file" in msg["parts"][0]["text"]
    # Metadata carries machine-readable hints for clients that want to render
    # a richer UI than a free-text prompt.
    metadata = update["metadata"]
    assert metadata["kind"] == "confirm_tool"
    assert metadata["tool_name"] == "delete_file"
    assert metadata["arguments"] == {"path": "/tmp/x"}

    # Run was persisted as paused with a snapshot of the pending tool call.
    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "paused"
    assert runs[0]["stop_reason"] == "input_required"
    async with db_engine.begin() as conn:
        pending = (
            await conn.execute(
                text("SELECT pending_action FROM agent_runs WHERE id = :r"),
                {"r": runs[0]["id"]},
            )
        ).scalar_one()
    assert pending["tool_calls"][0]["function"]["name"] == "delete_file"

    # Tool was NOT executed: no tool message in the context.
    async with db_engine.begin() as conn:
        ctx_id = (
            await conn.execute(
                text("SELECT id FROM agent_contexts WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()
    msgs = await fetch_messages(db_engine, ctx_id)
    assert [m["role"] for m in msgs] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_resume_with_yes_executes_destructive_tool(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    """Resume with 'yes' → tool runs, LLM final answer, run completes."""
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    # Use the safe tool descriptor for this test (the executor only checks the
    # destructiveHint annotation, not the actual tool behaviour).
    await _seed_mcp(
        db_engine,
        agent_id=agent_id,
        tools=[{**SAFE_TOOL, "name": "delete_file", "annotations": {"destructiveHint": True}}],
    )

    # Iter 1: LLM proposes destructive call → pause.
    # Iter 2 (after resume): LLM sees tool result, returns a final answer.
    stub = StubLlm()
    stub.add_tool_call(tool_name="delete_file", arguments={"q": "/tmp/x"})
    stub.add_text("Done.")
    install_llm_mock(respx_mock, stub)

    # First call: triggers pause.
    status1, events1, _ = await post_a2a(
        http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("delete /tmp/x")
    )
    assert status1 == 200
    last1 = next(e for e in reversed(events1) if is_status_event(e))
    assert event_state(last1) == "TASK_STATE_INPUT_REQUIRED"
    task_id = last1["statusUpdate"]["taskId"]
    context_id = last1["statusUpdate"]["contextId"]

    # Resume: same taskId + contextId, body = "yes".
    status2, events2, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("yes", task_id=task_id, context_id=context_id),
    )
    assert status2 == 200, events2
    last2 = next(e for e in reversed(events2) if is_status_event(e))
    assert event_state(last2) == "TASK_STATE_COMPLETED"
    artifacts = [e for e in events2 if is_artifact_event(e)]
    assert "".join(artifact_text(a) for a in artifacts) == "Done."

    # Run is now completed; pending_action cleared.
    runs = await fetch_runs_for_agent(db_engine, agent_id)
    # Resumed run reuses the same row, so still 1 run total.
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    async with db_engine.begin() as conn:
        pending = (
            await conn.execute(
                text("SELECT pending_action FROM agent_runs WHERE id = :r"),
                {"r": runs[0]["id"]},
            )
        ).scalar_one()
    assert pending is None


@pytest.mark.asyncio
async def test_resume_with_no_skips_tool_and_continues(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    """Resume with anything other than 'yes' → synthesize a 'user_denied' result."""
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    await _seed_mcp(
        db_engine,
        agent_id=agent_id,
        tools=[{**SAFE_TOOL, "name": "delete_file", "annotations": {"destructiveHint": True}}],
    )

    stub = StubLlm()
    stub.add_tool_call(tool_name="delete_file", arguments={"q": "/tmp/x"})
    stub.add_text("OK, I won't.")
    install_llm_mock(respx_mock, stub)

    _, events1, _ = await post_a2a(
        http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("delete /tmp/x")
    )
    last1 = next(e for e in reversed(events1) if is_status_event(e))
    task_id = last1["statusUpdate"]["taskId"]
    context_id = last1["statusUpdate"]["contextId"]

    _, events2, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("absolutely not", task_id=task_id, context_id=context_id),
    )
    last2 = next(e for e in reversed(events2) if is_status_event(e))
    assert event_state(last2) == "TASK_STATE_COMPLETED"

    # The tool result persisted in the context is the synthetic denial.
    async with db_engine.begin() as conn:
        ctx_id = (
            await conn.execute(
                text("SELECT id FROM agent_contexts WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()
    msgs = await fetch_messages(db_engine, ctx_id)
    tool_msg = next(m for m in msgs if m["role"] == "tool")
    assert tool_msg["content"] == "user_denied"


@pytest.mark.asyncio
async def test_safe_tool_runs_without_pause(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    """Tool without destructiveHint runs immediately, no pause."""
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    await _seed_mcp(db_engine, agent_id=agent_id, tools=[SAFE_TOOL])

    stub = StubLlm()
    stub.add_tool_call(tool_name="search", arguments={"q": "x"})
    stub.add_text("Found nothing.")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("search x")
    )
    assert status == 200
    states = [event_state(e) for e in events if is_status_event(e)]
    assert "TASK_STATE_INPUT_REQUIRED" not in states
    assert states[-1] == "TASK_STATE_COMPLETED"
    # Avoid noqa: keep the linters quiet about unused helpers.
    assert event_phase is not None and event_reason is not None
