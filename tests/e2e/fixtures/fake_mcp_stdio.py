"""A minimal, fully-functional MCP stdio server used as a discovery target in tests.

Run as ``python -m tests.e2e.fixtures.fake_mcp_stdio``. It exposes two tools,
``search`` and ``fetch``, each with a tiny JSON-Schema input. The server uses the
official MCP SDK so the protocol stays in sync with the client side under test.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

server = FastMCP("fake-mcp-stdio")


@server.tool()
def search(query: str) -> str:
    """Search the knowledge base for ``query`` and return the top match."""
    return f"results-for:{query}"


@server.tool()
def fetch(url: str) -> str:
    """Fetch ``url`` and return its body."""
    return f"body-of:{url}"


if __name__ == "__main__":
    server.run("stdio")
