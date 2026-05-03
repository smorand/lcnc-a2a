"""Shared executor scaffolding."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from lcnc_a2a.models.agent import Agent
from lcnc_a2a.models.agent_mcp_server import AgentMcpServer
from lcnc_a2a.models.agent_run import AgentRun


@dataclass(frozen=True, slots=True)
class ExecutorContext:
    """Inputs handed to an executor for one run."""

    agent: Agent
    run: AgentRun
    context_id: uuid.UUID
    user_text: str
    mcp_servers: list[AgentMcpServer]
    provider_api_key: str
    cancellation: asyncio.Event


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    """Coerce an OpenAI tool-call ``arguments`` field into a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def encode_decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


__all__ = [
    "AsyncIterator",
    "ExecutorContext",
    "encode_decimal",
    "parse_tool_arguments",
]
