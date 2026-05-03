"""US-004 acceptance tests for the streamable-HTTP MCP transport (E2E-036, 039)."""

from __future__ import annotations

import json
import os
import re
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._mcp_http_helpers import install_failure_mock, install_happy_path_mock


async def _csrf_for_agent_edit(client: httpx.AsyncClient, agent_id: uuid.UUID) -> str:
    response = await client.get(f"/agents/{agent_id}/edit")
    assert response.status_code == 200, response.text
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_e2e_036_http_discovery_happy_path(
    login_as,
    seed_user,
    seed_agent,
    db_engine: AsyncEngine,
    respx_mock,
) -> None:
    """E2E-036: streamable_http MCP discovery → tools_cache contains the search tool."""
    alice_id = await seed_user("alice@example.com", "Alice")
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await seed_agent(alice_id, name="agent-http")

    csrf = await _csrf_for_agent_edit(client, agent_id)

    install_happy_path_mock(respx_mock, url="https://mcp.example.com", tool_name="search")

    create_response = await client.post(
        f"/agents/{agent_id}/mcp",
        data={
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "headers": json.dumps({"X-Token": "t"}),
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
    assert "search" in discover_response.text

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT tools_cache, headers_enc FROM agent_mcp_servers WHERE id = :id"),
                {"id": server_id},
            )
        ).one()

    cache = row.tools_cache
    assert isinstance(cache, dict)
    tools = cache.get("tools")
    assert isinstance(tools, list)
    assert any(t["name"] == "search" for t in tools)

    fernet_key = os.environ["LCNC_A2A_ENCRYPTION_KEY"]
    decrypted = json.loads(Fernet(fernet_key.encode()).decrypt(row.headers_enc))
    assert decrypted == {"X-Token": "t"}


@pytest.mark.asyncio
async def test_e2e_039_http_discovery_rejects_non_2xx(
    login_as,
    seed_user,
    seed_agent,
    db_engine: AsyncEngine,
    respx_mock,
) -> None:
    """E2E-039: 500 response from the MCP HTTP endpoint → 422 'mcp_discovery_failed'."""
    alice_id = await seed_user("alice@example.com", "Alice")
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await seed_agent(alice_id, name="agent-http-fail")
    csrf = await _csrf_for_agent_edit(client, agent_id)

    install_failure_mock(respx_mock, url="https://mcp.example.com", status=500)

    create_response = await client.post(
        f"/agents/{agent_id}/mcp",
        data={
            "transport": "streamable_http",
            "url": "https://mcp.example.com",
            "headers": json.dumps({}),
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
    assert discover_response.status_code == 422, discover_response.text
    assert "mcp_discovery_failed" in discover_response.text
