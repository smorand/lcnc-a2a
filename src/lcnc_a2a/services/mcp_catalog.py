"""Curated catalog of suggested MCP servers.

Surfaced in the agent generate / edit form so users can attach a known-good
server in one click instead of typing transports and commands by hand.

Each entry is a small data class with everything the MCP form needs to
pre-fill itself, plus a short ``hint`` shown when the entry typically
requires a follow-up (e.g. an API key) before discovery will succeed.

The catalog is intentionally small and conservative; entries should point at
canonical, well-known servers that work out of the box on a fresh laptop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A single suggested MCP server."""

    id: str
    name: str
    description: str
    transport: str  # "stdio" or "streamable_http"
    command: str | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    headers: dict[str, str] | None = None
    hint: str | None = None  # one-line follow-up shown after creation


CATALOG: Final[tuple[CatalogEntry, ...]] = (
    CatalogEntry(
        id="duckduckgo",
        name="DuckDuckGo Search",
        description="Search the public web. Returns titles, URLs and snippets, no API key needed.",
        transport="stdio",
        command="uvx duckduckgo-mcp-server",
    ),
    CatalogEntry(
        id="fetch",
        name="Fetch",
        description="Download a URL and return its content. Pairs well with DuckDuckGo to read result pages.",
        transport="stdio",
        command="uvx mcp-server-fetch",
    ),
    CatalogEntry(
        id="context7",
        name="Context7",
        description="Up-to-date documentation lookups for popular libraries and frameworks.",
        transport="streamable_http",
        url="https://mcp.context7.com/mcp",
        hint="Add a Context7 API key as an Authorization header to lift the unauthenticated rate limit.",
    ),
)


CATALOG_BY_ID: Final[dict[str, CatalogEntry]] = {entry.id: entry for entry in CATALOG}


def get_entry(preset_id: str) -> CatalogEntry | None:
    """Return the catalog entry for ``preset_id``, or None if unknown."""
    return CATALOG_BY_ID.get(preset_id)
