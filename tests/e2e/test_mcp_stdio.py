"""US-004 acceptance tests for the stdio MCP transport (E2E-035, 037, 038, 040, 041)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lcnc_a2a.mcp_client.stdio import RECENT_SPAWNED_PIDS

STDIO_FAKE_SERVER = "python -m tests.e2e.fixtures.fake_mcp_stdio"
STDIO_HANG = "python -m tests.e2e.fixtures.fake_mcp_hang"
STDIO_FAIL = "python -m tests.e2e.fixtures.fake_mcp_fail"


async def _csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def _csrf_for_agent_edit(client: httpx.AsyncClient, agent_id: uuid.UUID) -> str:
    response = await client.get(f"/agents/{agent_id}/edit")
    assert response.status_code == 200, response.text
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def _seed_logged_in_alice(
    login_as,
    seed_user,
) -> tuple[httpx.AsyncClient, uuid.UUID]:
    user_id = await seed_user("alice@example.com", "Alice")
    client = await login_as("alice@example.com", name="Alice")
    return client, user_id


@pytest.mark.asyncio
async def test_e2e_035_stdio_discovery_happy_path(
    login_as,
    seed_user,
    seed_agent,
    db_engine: AsyncEngine,
) -> None:
    """E2E-035: add stdio MCP server then run discovery; tools_cache populated."""
    client, alice_id = await _seed_logged_in_alice(login_as, seed_user)
    agent_id = await seed_agent(alice_id, name="agent-A")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    response = await client.post(
        f"/agents/{agent_id}/mcp",
        data={
            "transport": "stdio",
            "command": STDIO_FAKE_SERVER,
            "env": json.dumps({"API_KEY": "k"}),
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 200, response.text

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, transport, command, env_enc, tools_cache, discovered_at "
                    "FROM agent_mcp_servers WHERE agent_id = :a"
                ),
                {"a": agent_id},
            )
        ).all()
    assert len(rows) == 1, rows
    server_id = rows[0].id
    assert rows[0].transport == "stdio"
    assert rows[0].command == STDIO_FAKE_SERVER
    assert rows[0].env_enc is not None
    assert rows[0].tools_cache is None
    assert rows[0].discovered_at is None

    discover_response = await client.post(
        f"/agents/{agent_id}/mcp/{server_id}/discover",
        data={"csrf_token": csrf},
    )
    assert discover_response.status_code == 200, discover_response.text
    assert "search" in discover_response.text
    assert "fetch" in discover_response.text

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT tools_cache, discovered_at, env_enc FROM agent_mcp_servers WHERE id = :id"),
                {"id": server_id},
            )
        ).one()

    cache = row.tools_cache
    assert isinstance(cache, dict)
    tools = cache.get("tools")
    assert isinstance(tools, list)
    names = {t["name"] for t in tools}
    assert names == {"search", "fetch"}
    for tool in tools:
        assert tool["description"]
        assert tool["input_schema"] is not None
        assert isinstance(tool["input_schema"], dict)
    assert row.discovered_at is not None
    assert datetime.now(UTC) - row.discovered_at < timedelta(seconds=15)

    fernet_key = os.environ["LCNC_A2A_ENCRYPTION_KEY"]
    decrypted = json.loads(Fernet(fernet_key.encode()).decrypt(row.env_enc))
    assert decrypted == {"API_KEY": "k"}


@pytest.mark.asyncio
async def test_e2e_037_stdio_discovery_failure_propagates_stderr(
    login_as,
    seed_user,
    seed_agent,
    db_engine: AsyncEngine,
) -> None:
    """E2E-037: stdio command exits 1 → 422 + body includes 'mcp_discovery_failed' and 'boom'."""
    client, alice_id = await _seed_logged_in_alice(login_as, seed_user)
    agent_id = await seed_agent(alice_id, name="agent-fail")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    create_response = await client.post(
        f"/agents/{agent_id}/mcp",
        data={"transport": "stdio", "command": STDIO_FAIL, "csrf_token": csrf},
    )
    assert create_response.status_code == 200, create_response.text
    async with db_engine.begin() as conn:
        server_id = (
            await conn.execute(
                text("SELECT id FROM agent_mcp_servers WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()

    response = await client.post(
        f"/agents/{agent_id}/mcp/{server_id}/discover",
        data={"csrf_token": csrf},
    )
    assert response.status_code == 422, response.text
    assert "mcp_discovery_failed" in response.text
    assert "boom" in response.text

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT tools_cache, discovered_at FROM agent_mcp_servers WHERE id = :id"),
                {"id": server_id},
            )
        ).one()
    assert row.tools_cache is None
    assert row.discovered_at is None


@pytest.mark.asyncio
async def test_e2e_038_stdio_discovery_timeout_kills_process(
    login_as,
    seed_user,
    seed_agent,
    db_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E2E-038: hanging stdio → 422 'mcp_discovery_timeout' and child process killed."""
    monkeypatch.setattr("lcnc_a2a.mcp_client.stdio.DISCOVERY_TIMEOUT_S", 2.0)
    RECENT_SPAWNED_PIDS.clear()

    client, alice_id = await _seed_logged_in_alice(login_as, seed_user)
    agent_id = await seed_agent(alice_id, name="agent-hang")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    create_response = await client.post(
        f"/agents/{agent_id}/mcp",
        data={"transport": "stdio", "command": STDIO_HANG, "csrf_token": csrf},
    )
    assert create_response.status_code == 200, create_response.text
    async with db_engine.begin() as conn:
        server_id = (
            await conn.execute(
                text("SELECT id FROM agent_mcp_servers WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()

    start = time.monotonic()
    response = await client.post(
        f"/agents/{agent_id}/mcp/{server_id}/discover",
        data={"csrf_token": csrf},
    )
    elapsed = time.monotonic() - start
    assert response.status_code == 422, response.text
    assert "mcp_discovery_timeout" in response.text
    assert elapsed < 11.0

    spawned = list(RECENT_SPAWNED_PIDS)
    assert spawned, "discovery did not record any spawned PIDs"
    deadline = time.monotonic() + 5.0
    for pid in spawned:
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                pytest.fail(f"PID {pid} still alive after timeout cleanup")
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_e2e_040_env_values_never_in_response(
    login_as,
    seed_user,
    seed_agent,
    db_engine: AsyncEngine,
) -> None:
    """E2E-040: GET /agents/{a}/mcp/{s} masks env values with ******** and never leaks plaintext."""
    plaintext = "topsecret-MCP-value-XYZ"
    client, alice_id = await _seed_logged_in_alice(login_as, seed_user)
    agent_id = await seed_agent(alice_id, name="agent-secret")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    create_response = await client.post(
        f"/agents/{agent_id}/mcp",
        data={
            "transport": "stdio",
            "command": "/bin/true",
            "env": json.dumps({"SECRET": plaintext}),
            "csrf_token": csrf,
        },
    )
    assert create_response.status_code == 200, create_response.text
    async with db_engine.begin() as conn:
        server_id = (
            await conn.execute(
                text("SELECT id FROM agent_mcp_servers WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()

    response = await client.get(f"/agents/{agent_id}/mcp/{server_id}")
    assert response.status_code == 200, response.text
    assert "********" in response.text
    assert plaintext not in response.text
    for value in response.headers.values():
        assert plaintext not in value


@pytest.mark.asyncio
async def test_e2e_041_transport_change_requires_rediscovery(
    login_as,
    seed_user,
    seed_agent,
    db_engine: AsyncEngine,
    respx_mock,
) -> None:
    """E2E-041: switching transport with stale tools_cache is rejected with 409."""
    client, alice_id = await _seed_logged_in_alice(login_as, seed_user)
    agent_id = await seed_agent(alice_id, name="agent-transport")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    create_response = await client.post(
        f"/agents/{agent_id}/mcp",
        data={
            "transport": "stdio",
            "command": STDIO_FAKE_SERVER,
            "csrf_token": csrf,
        },
    )
    assert create_response.status_code == 200, create_response.text
    async with db_engine.begin() as conn:
        server_id = (
            await conn.execute(
                text("SELECT id FROM agent_mcp_servers WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).scalar_one()
    discover_response = await client.post(
        f"/agents/{agent_id}/mcp/{server_id}/discover",
        data={"csrf_token": csrf},
    )
    assert discover_response.status_code == 200, discover_response.text

    update_response = await client.post(
        f"/agents/{agent_id}/mcp/{server_id}",
        data={
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "headers": json.dumps({}),
            "csrf_token": csrf,
        },
    )
    assert update_response.status_code == 409, update_response.text
    assert "rediscovery_required" in update_response.text

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT transport FROM agent_mcp_servers WHERE id = :id"),
                {"id": server_id},
            )
        ).one()
    assert row.transport == "stdio"

    from tests.e2e._mcp_http_helpers import install_happy_path_mock

    install_happy_path_mock(respx_mock, url="https://mcp.example.com", tool_name="search")

    discover_again = await client.post(
        f"/agents/{agent_id}/mcp/{server_id}/discover",
        data={
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "headers": json.dumps({}),
            "csrf_token": csrf,
        },
    )
    assert discover_again.status_code == 200, discover_again.text

    update_again = await client.post(
        f"/agents/{agent_id}/mcp/{server_id}",
        data={
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "headers": json.dumps({}),
            "csrf_token": csrf,
        },
    )
    assert update_again.status_code in (200, 302), update_again.text

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT transport FROM agent_mcp_servers WHERE id = :id"),
                {"id": server_id},
            )
        ).one()
    assert row.transport == "streamable_http"
