"""MCP stdio fixture for US-007 Plan & Execute tests.

Exposes ``search``, ``get_market_data``, ``compute_ratios``, ``echo``, and
``slow`` tools. Each tool records its invocation to a per-tool touch file
when the matching env var is set.

Run as ``python -m tests.e2e.fixtures.fake_mcp_pe``.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("fake-mcp-pe")


def _touch(env_key: str, payload: str = "call\n") -> None:
    target = os.environ.get(env_key)
    if not target:
        return
    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(payload)


@server.tool()
def search(query: str = "") -> str:
    """Stub search tool."""
    _touch("FAKE_MCP_PE_SEARCH_TOUCH", f"search:{query}\n")
    return f"search-results:{query}"


@server.tool()
def get_market_data(symbol: str = "") -> str:
    """Stub market-data tool."""
    _touch("FAKE_MCP_PE_MARKET_TOUCH", f"market:{symbol}\n")
    return f"market-data:{symbol}"


@server.tool()
def compute_ratios(input_value: str = "") -> str:
    """Stub ratios tool."""
    _touch("FAKE_MCP_PE_RATIOS_TOUCH", f"ratios:{input_value}\n")
    return f"ratios:{input_value}"


@server.tool()
def echo(value: str = "") -> str:
    """Echo the input value for substitution tests."""
    _touch("FAKE_MCP_PE_ECHO_TOUCH", f"echo:{value}\n")
    return value


@server.tool()
def slow() -> str:
    """Sleep for ~200 ms then return; used for parallel-stage timing."""
    _touch("FAKE_MCP_PE_SLOW_TOUCH", "slow\n")
    time.sleep(0.2)
    return "slow-done"


if __name__ == "__main__":
    sys.stderr.write("fake-mcp-pe ready\n")
    server.run("stdio")
