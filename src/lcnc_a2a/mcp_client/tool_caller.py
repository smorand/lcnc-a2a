"""MCP tool-call helpers (stdio + streamable_http)."""

from __future__ import annotations

import asyncio
import contextlib
import shlex
import tempfile
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from lcnc_a2a.mcp_client.errors import McpDiscoveryError
from lcnc_a2a.mcp_client.stdio import scrub_env

DEFAULT_TOOL_TIMEOUT_S = 30.0


class McpToolError(Exception):
    """Raised when a tool call fails (timeout, transport, malformed envelope)."""


def _content_to_text(blocks: Any) -> str:
    if blocks is None:
        return ""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
            continue
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


async def call_tool_stdio(
    *,
    command: str,
    env: dict[str, str] | None,
    cwd: str | None,
    tool_name: str,
    arguments: dict[str, Any],
    timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
) -> dict[str, Any]:
    """Spawn an MCP stdio server, call ``tool_name``, return ``{is_error, content}``."""
    parts = shlex.split(command)
    if not parts:
        raise McpToolError("empty command")
    cmd, *args = parts

    server_params = StdioServerParameters(
        command=cmd,
        args=args,
        env=scrub_env(env),
        cwd=cwd,
    )

    stderr_handle: Any = tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", delete=False)  # noqa: SIM115
    try:
        async with asyncio.timeout(timeout_s):
            async with (
                stdio_client(server_params, errlog=stderr_handle) as (read_stream, write_stream),
                ClientSession(read_stream, write_stream) as session,
            ):
                await session.initialize()
                result = await session.call_tool(tool_name, arguments=arguments)
        return _normalize_result(result)
    except McpDiscoveryError:
        raise
    except (TimeoutError, asyncio.CancelledError) as exc:
        raise McpToolError(f"tool_timeout:{tool_name}") from exc
    except McpToolError:
        raise
    except Exception as exc:
        raise McpToolError(f"tool_failed:{exc}") from exc
    finally:
        with contextlib.suppress(OSError):
            stderr_handle.close()


async def call_tool_http(
    *,
    url: str,
    headers: dict[str, str] | None,
    tool_name: str,
    arguments: dict[str, Any],
    timeout_s: float = DEFAULT_TOOL_TIMEOUT_S,
) -> dict[str, Any]:
    """Call ``tool_name`` over a streamable-HTTP MCP server."""
    try:
        async with asyncio.timeout(timeout_s):
            async with streamablehttp_client(url, headers=headers or {}) as (
                read_stream,
                write_stream,
                _session_id,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments)
        return _normalize_result(result)
    except (TimeoutError, asyncio.CancelledError) as exc:
        raise McpToolError(f"tool_timeout:{tool_name}") from exc
    except Exception as exc:
        raise McpToolError(f"tool_failed:{exc}") from exc


def _normalize_result(result: Any) -> dict[str, Any]:
    """Coerce an MCP ``CallToolResult`` into a plain JSON-friendly dict."""
    is_error = bool(getattr(result, "isError", False))
    content_blocks = getattr(result, "content", None)
    text = _content_to_text(content_blocks)
    structured = getattr(result, "structuredContent", None)
    payload: dict[str, Any] = {"is_error": is_error, "content": text}
    if structured is not None:
        payload["structured"] = structured
    return payload
