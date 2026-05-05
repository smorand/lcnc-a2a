"""US-005 acceptance tests for tool calls in Simple mode (E2E-042, 049, 053, 054)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lcnc_a2a.executors import simple as simple_executor
from tests.e2e._a2a_helpers import (
    StubLlm,
    artifact_text,
    event_reason,
    event_state,
    fetch_messages,
    fetch_runs_for_agent,
    fetch_steps,
    install_llm_mock,
    is_artifact_event,
    make_a2a_envelope,
    post_a2a,
    seed_mcp_server_with_cache,
    seed_started_agent,
)

FAKE_MCP_ADD = "python -m tests.e2e.fixtures.fake_mcp_add"

ADD_TOOL = {
    "name": "add",
    "description": "Add two numbers.",
    "input_schema": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
}

FLAKY_TOOL = {
    "name": "flaky",
    "description": "Always errors.",
    "input_schema": {"type": "object", "properties": {}},
}

NOOP_TOOL = {
    "name": "noop",
    "description": "No-op tool.",
    "input_schema": {"type": "object", "properties": {}},
}


@pytest.mark.asyncio
async def test_e2e_042_mcp_tools_presented_in_openai_format(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    await seed_mcp_server_with_cache(
        db_engine,
        agent_id=agent_id,
        command="/bin/true",
        tools=[
            {
                "name": "search",
                "description": "Web search",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            }
        ],
    )
    stub = StubLlm()
    stub.add_text("done")
    install_llm_mock(respx_mock, stub)

    status, _events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("hi"),
    )
    assert status == 200
    assert stub.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "search",
                "description": "Web search",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]


@pytest.mark.asyncio
async def test_e2e_049_simple_with_one_tool_call(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path: Path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    touch_file = tmp_path / "add_calls.log"
    async with db_engine.begin() as conn:
        # We need to populate env so the fixture writes to a tmp file we can inspect.
        pass
    # Insert MCP server using direct SQL (bypassing crypto for test simplicity).
    import json
    import uuid as _uuid

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
                "cache": json.dumps({"tools": [ADD_TOOL]}),
                "env_enc": _encrypt_env({"FAKE_MCP_ADD_TOUCH_FILE": str(touch_file)}),
            },
        )

    stub = StubLlm()
    stub.add_tool_call(
        tool_name="add",
        arguments={"a": 2, "b": 3},
        prompt_tokens=5,
        completion_tokens=5,
        cost=0.0001,
    )
    stub.add_text("The answer is 5", prompt_tokens=5, completion_tokens=5, cost=0.0001)
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("What is 2+3?"),
    )
    assert status == 200, events
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"
    artifacts = [e for e in events if is_artifact_event(e)]
    rendered = "".join(artifact_text(a) for a in artifacts)
    assert rendered == "The answer is 5"

    # MCP fixture recorded exactly one call.
    assert touch_file.exists()
    assert touch_file.read_text().count("call") == 1

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    steps = await fetch_steps(db_engine, runs[0]["id"])
    roles = [s["role"] for s in steps]
    assert roles == ["assistant", "tool", "assistant"]
    tool_step = steps[1]
    assert tool_step["tool_name"] == "add"
    assert tool_step["tool_result_json"]["content"] == "5"

    async with db_engine.begin() as conn:
        context_id = (
            await conn.execute(
                text("SELECT id FROM agent_contexts WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()
    msgs = await fetch_messages(db_engine, context_id)
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1]["tool_call_json"] is not None


@pytest.mark.asyncio
async def test_e2e_053_tool_failure_3_attempts_then_continue(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(simple_executor, "TOOL_RETRY_BACKOFFS", (0.01, 0.01, 0.01))

    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    flaky_log = tmp_path / "flaky_calls.log"
    import json
    import uuid as _uuid

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, command, "
                "tools_cache, env_enc) VALUES (:id, :agent_id, 'stdio', :command, "
                ":cache, :env_enc)"
            ),
            {
                "id": _uuid.uuid4(),
                "agent_id": agent_id,
                "command": FAKE_MCP_ADD,
                "cache": json.dumps({"tools": [FLAKY_TOOL]}),
                "env_enc": _encrypt_env({"FAKE_MCP_FLAKY_TOUCH_FILE": str(flaky_log)}),
            },
        )

    stub = StubLlm()
    stub.add_tool_call(tool_name="flaky", arguments={})
    stub.add_text("tool failed")
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("call flaky"),
    )
    assert status == 200, events
    assert event_state(events[-1]) == "TASK_STATE_COMPLETED"

    assert flaky_log.exists()
    assert flaky_log.read_text().count("call") == 3

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "completed"
    steps = await fetch_steps(db_engine, runs[0]["id"])
    tool_step = next(s for s in steps if s["role"] == "tool")
    assert tool_step["tool_result_json"]["is_error"] is True


@pytest.mark.asyncio
async def test_e2e_054_simple_defensive_cap_50_iterations(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path: Path,
) -> None:
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")
    noop_log = tmp_path / "noop_calls.log"
    import json
    import uuid as _uuid

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, command, "
                "tools_cache, env_enc) VALUES (:id, :agent_id, 'stdio', :command, "
                ":cache, :env_enc)"
            ),
            {
                "id": _uuid.uuid4(),
                "agent_id": agent_id,
                "command": FAKE_MCP_ADD,
                "cache": json.dumps({"tools": [NOOP_TOOL]}),
                "env_enc": _encrypt_env({"FAKE_MCP_NOOP_TOUCH_FILE": str(noop_log)}),
            },
        )

    stub = StubLlm(repeat_last=True)
    stub.add_tool_call(tool_name="noop", arguments={})
    install_llm_mock(respx_mock, stub)

    status, events, _ = await post_a2a(
        http_client,
        agent_id=agent_id,
        plain_key=plain,
        body=make_a2a_envelope("infinite tool"),
    )
    assert status == 200, events
    assert event_state(events[-1]) == "TASK_STATE_FAILED"
    assert event_reason(events[-1]) == "guardrail_exceeded"
    runs = await fetch_runs_for_agent(db_engine, agent_id)
    assert runs[0]["status"] == "failed"
    assert runs[0]["stop_reason"] == "guardrail_exceeded"
    assert noop_log.exists()
    assert noop_log.read_text().count("call") == 50


def _encrypt_env(env: dict[str, str]) -> bytes:
    import json
    import os

    from cryptography.fernet import Fernet

    fernet = Fernet(os.environ["LCNC_A2A_ENCRYPTION_KEY"].encode())
    return fernet.encrypt(json.dumps(env, sort_keys=True).encode("utf-8"))
