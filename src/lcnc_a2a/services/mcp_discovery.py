"""High-level MCP discovery facade and persistence helpers."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.mcp_client.errors import McpDiscoveryError, McpDiscoveryTimeoutError
from lcnc_a2a.mcp_client.streamable_http import discover_http
from lcnc_a2a.models.agent_mcp_server import AgentMcpServer

TRANSPORT_STDIO = "stdio"
TRANSPORT_STREAMABLE_HTTP = "streamable_http"
SUPPORTED_TRANSPORTS = (TRANSPORT_STDIO, TRANSPORT_STREAMABLE_HTTP)


class TransportRediscoveryRequiredError(Exception):
    """Raised when a save would change the transport without a fresh discovery."""


class InvalidMcpFormError(ValueError):
    """Raised when the submitted MCP form fields are invalid."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_tools(raw_tools: list[Any]) -> list[dict[str, Any]]:
    """Convert MCP SDK tool objects (or dicts) to the canonical cache format."""
    normalized: list[dict[str, Any]] = []
    for tool in raw_tools:
        if isinstance(tool, dict):
            name = tool.get("name", "")
            description = tool.get("description") or ""
            schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        else:
            name = getattr(tool, "name", "")
            description = getattr(tool, "description", None) or ""
            schema = getattr(tool, "inputSchema", None) or {}
        normalized.append(
            {
                "name": name,
                "description": description,
                "input_schema": schema,
            }
        )
    return normalized


def parse_json_map(raw: str, *, field: str) -> dict[str, str]:
    """Parse a JSON object literal into a dict of string keys/values."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidMcpFormError(f"{field}_invalid") from exc
    if not isinstance(parsed, dict):
        raise InvalidMcpFormError(f"{field}_invalid")
    out: dict[str, str] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InvalidMcpFormError(f"{field}_invalid")
        out[key] = value
    return out


async def get_server_for_agent(
    db: AsyncSession,
    *,
    server_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> AgentMcpServer | None:
    """Fetch a server row only if it belongs to ``agent_id`` (404 leak protection)."""
    result = await db.execute(
        select(AgentMcpServer).where(
            AgentMcpServer.id == server_id,
            AgentMcpServer.agent_id == agent_id,
        )
    )
    return result.scalar_one_or_none()


async def list_servers_for_agent(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
) -> list[AgentMcpServer]:
    """Return every MCP server attached to an agent, ordered by creation."""
    result = await db.execute(
        select(AgentMcpServer).where(AgentMcpServer.agent_id == agent_id).order_by(AgentMcpServer.id)
    )
    return list(result.scalars().all())


async def create_server(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    transport: str,
    command: str | None,
    env: dict[str, str] | None,
    cwd: str | None,
    url: str | None,
    headers: dict[str, str] | None,
    crypto: CryptoService,
) -> AgentMcpServer:
    """Persist a new ``agent_mcp_servers`` row, encrypting env/headers at rest."""
    if transport not in SUPPORTED_TRANSPORTS:
        raise InvalidMcpFormError("transport_invalid")
    if transport == TRANSPORT_STDIO:
        if not command:
            raise InvalidMcpFormError("command_required")
        url = None
        headers = None
    else:
        if not url:
            raise InvalidMcpFormError("url_required")
        command = None
        env = None
        cwd = None

    row = AgentMcpServer(
        agent_id=agent_id,
        transport=transport,
        command=command,
        env_enc=_encrypt_map(env, crypto),
        cwd=cwd,
        url=url,
        headers_enc=_encrypt_map(headers, crypto),
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


async def update_server(
    db: AsyncSession,
    *,
    row: AgentMcpServer,
    transport: str,
    command: str | None,
    env: dict[str, str] | None,
    cwd: str | None,
    url: str | None,
    headers: dict[str, str] | None,
    crypto: CryptoService,
) -> AgentMcpServer:
    """Update fields on an existing MCP-server row.

    If the persisted row already carries a ``tools_cache`` populated under a different
    transport, the caller is forced to re-run discovery first.
    """
    if transport not in SUPPORTED_TRANSPORTS:
        raise InvalidMcpFormError("transport_invalid")

    if row.transport != transport and row.tools_cache is not None:
        raise TransportRediscoveryRequiredError("rediscovery_required")

    if transport == TRANSPORT_STDIO:
        if not command:
            raise InvalidMcpFormError("command_required")
        row.transport = TRANSPORT_STDIO
        row.command = command
        row.env_enc = _encrypt_map(env, crypto)
        row.cwd = cwd
        row.url = None
        row.headers_enc = None
    else:
        if not url:
            raise InvalidMcpFormError("url_required")
        row.transport = TRANSPORT_STREAMABLE_HTTP
        row.url = url
        row.headers_enc = _encrypt_map(headers, crypto)
        row.command = None
        row.env_enc = None
        row.cwd = None
    await db.flush()
    await db.refresh(row)
    return row


async def delete_server(db: AsyncSession, *, row: AgentMcpServer) -> None:
    """Delete an MCP-server row."""
    await db.delete(row)


async def run_discovery(
    *,
    row: AgentMcpServer,
    crypto: CryptoService,
) -> list[dict[str, Any]]:
    """Run discovery against ``row``'s configuration. Raises on failure / timeout."""
    if row.transport == TRANSPORT_STDIO:
        from lcnc_a2a.mcp_client.stdio import discover_stdio

        env = _decrypt_map(row.env_enc, crypto)
        return await discover_stdio(command=row.command or "", env=env, cwd=row.cwd)
    if row.transport == TRANSPORT_STREAMABLE_HTTP:
        headers = _decrypt_map(row.headers_enc, crypto)
        return await discover_http(url=row.url or "", headers=headers)
    raise InvalidMcpFormError("transport_invalid")


async def persist_discovery_result(
    db: AsyncSession,
    *,
    row: AgentMcpServer,
    tools: list[dict[str, Any]],
) -> AgentMcpServer:
    """Store the discovered tools list and bump ``discovered_at``."""
    row.tools_cache = {"tools": tools}
    row.discovered_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(row)
    return row


def decrypt_env(row: AgentMcpServer, crypto: CryptoService) -> dict[str, str]:
    """Decrypt the ``env_enc`` blob into a plain dict (or empty)."""
    return _decrypt_map(row.env_enc, crypto)


def decrypt_headers(row: AgentMcpServer, crypto: CryptoService) -> dict[str, str]:
    """Decrypt the ``headers_enc`` blob into a plain dict (or empty)."""
    return _decrypt_map(row.headers_enc, crypto)


def masked_env_keys(row: AgentMcpServer, crypto: CryptoService) -> list[str]:
    """Return the env variable names (values masked); used by templates."""
    return list(_decrypt_map(row.env_enc, crypto).keys())


def masked_header_keys(row: AgentMcpServer, crypto: CryptoService) -> list[str]:
    """Return the header names (values masked); used by templates."""
    return list(_decrypt_map(row.headers_enc, crypto).keys())


def _encrypt_map(data: dict[str, str] | None, crypto: CryptoService) -> bytes | None:
    if data is None:
        return None
    return crypto.encrypt(json.dumps(data, sort_keys=True).encode("utf-8"))


def _decrypt_map(blob: bytes | None, crypto: CryptoService) -> dict[str, str]:
    if blob is None:
        return {}
    raw = crypto.decrypt(blob)
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items()}


__all__ = [
    "SUPPORTED_TRANSPORTS",
    "TRANSPORT_STDIO",
    "TRANSPORT_STREAMABLE_HTTP",
    "InvalidMcpFormError",
    "McpDiscoveryError",
    "McpDiscoveryTimeoutError",
    "TransportRediscoveryRequiredError",
    "create_server",
    "decrypt_env",
    "decrypt_headers",
    "delete_server",
    "get_server_for_agent",
    "list_servers_for_agent",
    "masked_env_keys",
    "masked_header_keys",
    "normalize_tools",
    "parse_json_map",
    "persist_discovery_result",
    "run_discovery",
    "update_server",
]
