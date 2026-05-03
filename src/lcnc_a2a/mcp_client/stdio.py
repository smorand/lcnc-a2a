"""Stdio MCP discovery client wrapping `mcp.client.stdio.stdio_client`.

The wrapper enforces:
  - 10s wall-clock timeout for the entire `initialize + tools/list` exchange
  - parent-environment scrubbing (only the explicit ``env`` map plus ``PATH`` is forwarded)
  - subprocess termination on timeout / failure (delegated to the SDK)
  - stderr capture via a temporary file (truncated to 2 KB)
  - PID tracking so tests can assert the subprocess is gone post-discovery
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from lcnc_a2a.mcp_client.errors import McpDiscoveryError, McpDiscoveryTimeoutError

DISCOVERY_TIMEOUT_S = 10.0
STDERR_TRUNCATE_BYTES = 2048

# Test-observable list of PIDs spawned by the most recent discovery calls.
RECENT_SPAWNED_PIDS: list[int] = []


def _scrub_env(user_env: dict[str, str] | None) -> dict[str, str]:
    """Build the subprocess env: only PATH from parent + caller-provided values."""
    env: dict[str, str] = {}
    parent_path = os.environ.get("PATH")
    if parent_path is not None:
        env["PATH"] = parent_path
    if user_env:
        env.update(user_env)
    return env


def _read_stderr_file(path: str) -> str:
    """Read up to STDERR_TRUNCATE_BYTES bytes from the captured stderr file."""
    try:
        data = Path(path).read_bytes()[: STDERR_TRUNCATE_BYTES + 1]
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    if len(text) > STDERR_TRUNCATE_BYTES:
        return text[:STDERR_TRUNCATE_BYTES]
    return text


async def discover_stdio(
    *,
    command: str,
    env: dict[str, str] | None,
    cwd: str | None,
) -> list[dict[str, Any]]:
    """Spawn an MCP stdio server, fetch ``tools/list``, return normalized tools."""
    parts = shlex.split(command)
    if not parts:
        raise McpDiscoveryError("mcp_discovery_failed", detail="empty command")
    cmd, *args = parts

    server_params = StdioServerParameters(
        command=cmd,
        args=args,
        env=_scrub_env(env),
        cwd=cwd,
    )

    stderr_handle = tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", delete=False)  # noqa: SIM115
    stderr_path = stderr_handle.name
    try:
        try:
            async with asyncio.timeout(DISCOVERY_TIMEOUT_S):
                return await _run_stdio_session(server_params, stderr_handle)
        except (TimeoutError, asyncio.CancelledError) as exc:
            stderr_handle.close()
            raise McpDiscoveryTimeoutError(
                "mcp_discovery_timeout",
                detail=_read_stderr_file(stderr_path),
            ) from exc
        except McpDiscoveryError:
            stderr_handle.close()
            raise
        except Exception as exc:
            stderr_handle.close()
            raise McpDiscoveryError(
                "mcp_discovery_failed",
                detail=_read_stderr_file(stderr_path),
            ) from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(stderr_path)  # noqa: PTH108


async def _run_stdio_session(
    server_params: StdioServerParameters,
    stderr_handle: Any,
) -> list[dict[str, Any]]:
    """Open the stdio transport, perform the handshake, return normalized tools."""
    from lcnc_a2a.services.mcp_discovery import normalize_tools

    real_open_process = anyio.open_process

    async def _tracking_open_process(*args: Any, **kwargs: Any) -> Any:
        process = await real_open_process(*args, **kwargs)
        if getattr(process, "pid", None) is not None:
            RECENT_SPAWNED_PIDS.append(int(process.pid))
        return process

    anyio.open_process = _tracking_open_process
    try:
        async with (
            stdio_client(server_params, errlog=stderr_handle) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            response = await session.list_tools()
            return normalize_tools(response.tools)
    except McpDiscoveryError:
        raise
    except Exception as exc:
        with contextlib.suppress(OSError, ValueError):
            stderr_handle.flush()
        raise McpDiscoveryError(
            "mcp_discovery_failed",
            detail=_read_stderr_file(stderr_handle.name),
        ) from exc
    finally:
        anyio.open_process = real_open_process  # restore patched function  # type: ignore[assignment]
