"""Helpers for mocking the MCP streamable-HTTP protocol with respx."""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx


def install_happy_path_mock(
    mock: respx.Router,
    *,
    url: str,
    tool_name: str = "search",
    tool_description: str = "Search the knowledge base.",
) -> None:
    """Mock a fully-functional MCP streamable-HTTP server exposing a single tool."""
    base = url.rstrip("/")

    def _handle(request: httpx.Request) -> httpx.Response:
        try:
            message = json.loads(request.content.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return httpx.Response(202)
        method = message.get("method", "")
        msg_id = message.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mock-mcp", "version": "0.0.1"},
                    },
                },
            )
        if method == "tools/list":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": tool_name,
                                "description": tool_description,
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"query": {"type": "string"}},
                                },
                            }
                        ]
                    },
                },
            )
        return httpx.Response(202)

    mock.post(base).mock(side_effect=_handle)
    mock.post(base + "/").mock(side_effect=_handle)


def install_failure_mock(
    mock: respx.Router,
    *,
    url: str,
    status: int = 500,
    body: str = "internal error",
) -> None:
    """Mock the URL with a non-2xx response so the SDK raises HTTPStatusError."""
    base = url.rstrip("/")
    mock.post(base).mock(return_value=httpx.Response(status, text=body))
    mock.post(base + "/").mock(return_value=httpx.Response(status, text=body))


__all__: list[Any] = ["install_failure_mock", "install_happy_path_mock"]
