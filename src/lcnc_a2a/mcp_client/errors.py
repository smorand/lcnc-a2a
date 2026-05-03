"""Discovery-failure exception types shared by both transports."""

from __future__ import annotations


class McpDiscoveryError(Exception):
    """A discovery attempt failed (non-zero exit, non-2xx, invalid envelope, ...)."""

    code: str = "mcp_discovery_failed"

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class McpDiscoveryTimeoutError(McpDiscoveryError):
    """Discovery exceeded the 10-second wall-clock budget."""

    code = "mcp_discovery_timeout"
