"""MCP stdio server with ``add`` and ``flaky`` tools used by US-005 tests.

Run as ``python -m tests.e2e.fixtures.fake_mcp_add``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("fake-mcp-add")
TOUCH_FILE = os.environ.get("FAKE_MCP_ADD_TOUCH_FILE")
FLAKY_TOUCH_FILE = os.environ.get("FAKE_MCP_FLAKY_TOUCH_FILE")
NOOP_TOUCH_FILE = os.environ.get("FAKE_MCP_NOOP_TOUCH_FILE")


def _touch(path: str | None) -> None:
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("call\n")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    _touch(TOUCH_FILE)
    return a + b


@server.tool()
def flaky() -> str:
    """Always raise."""
    _touch(FLAKY_TOUCH_FILE)
    raise ValueError("flaky-tool-error")


@server.tool()
def noop() -> str:
    """No-op tool used for the iteration cap test."""
    _touch(NOOP_TOUCH_FILE)
    return "ok"


if __name__ == "__main__":
    sys.stderr.write("fake-mcp-add ready\n")
    server.run("stdio")
