"""Streamable-HTTP MCP discovery client wrapping `mcp.client.streamable_http`."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from lcnc_a2a.mcp_client.errors import McpDiscoveryError, McpDiscoveryTimeoutError

DISCOVERY_TIMEOUT_S = 10.0
RESPONSE_BODY_TRUNCATE_BYTES = 2048


async def discover_http(
    *,
    url: str,
    headers: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """Connect over streamable HTTP, fetch ``tools/list``, return normalized tools."""
    from lcnc_a2a.services.mcp_discovery import normalize_tools

    try:
        async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
            async with streamablehttp_client(url, headers=headers or {}) as (
                read_stream,
                write_stream,
                _session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    return normalize_tools(response.tools)
    except (TimeoutError, asyncio.CancelledError) as exc:
        raise McpDiscoveryTimeoutError("mcp_discovery_timeout") from exc
    except httpx.HTTPStatusError as exc:
        body_excerpt = ""
        if exc.response is not None:
            try:
                body_excerpt = exc.response.text[:RESPONSE_BODY_TRUNCATE_BYTES]
            except Exception:
                body_excerpt = ""
        raise McpDiscoveryError("mcp_discovery_failed", detail=body_excerpt) from exc
    except httpx.HTTPError as exc:
        raise McpDiscoveryError("mcp_discovery_failed", detail=str(exc)[:RESPONSE_BODY_TRUNCATE_BYTES]) from exc
    except McpDiscoveryError:
        raise
    except Exception as exc:
        raise McpDiscoveryError("mcp_discovery_failed", detail=str(exc)[:RESPONSE_BODY_TRUNCATE_BYTES]) from exc
