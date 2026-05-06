"""Shared executor scaffolding."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from dataclasses import field as dataclasses_field
from decimal import Decimal
from typing import Any

from lcnc_a2a.a2a.sse import A2AEventEmitter
from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.llm.provider import ChatResponse
from lcnc_a2a.mcp_client.tool_caller import McpToolError, call_tool_http, call_tool_stdio
from lcnc_a2a.models.agent import Agent
from lcnc_a2a.models.agent_mcp_server import AgentMcpServer
from lcnc_a2a.models.agent_run import AgentRun
from lcnc_a2a.services.mcp_discovery import decrypt_env, decrypt_headers

TOOL_RETRY_BACKOFFS: tuple[float, ...] = (0.2, 0.6, 1.8)


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
    emitter: A2AEventEmitter
    # Custom HTTP headers to send on every LLM call (decrypted from the
    # agent's ``provider_extra_headers_enc`` column).
    provider_extra_headers: dict[str, str] = dataclasses_field(default_factory=dict)
    # When set, this is a resume of a previously paused run. The executor
    # consumes ``resume_action`` (the snapshot the pause path persisted into
    # ``agent_runs.pending_action``) and treats ``user_text`` as the user's
    # answer to the input_required prompt.
    resume_action: dict[str, Any] | None = None


def collect_tools(servers: list[AgentMcpServer]) -> list[dict[str, Any]]:
    """Flatten ``tools_cache`` across all attached MCP servers."""
    out: list[dict[str, Any]] = []
    for server in servers:
        cache = server.tools_cache or {}
        tools = cache.get("tools") if isinstance(cache, dict) else None
        if not isinstance(tools, list):
            continue
        for descriptor in tools:
            if not isinstance(descriptor, dict):
                continue
            out.append({"server": server, "descriptor": descriptor})
    return out


def needs_confirmation(descriptor: dict[str, Any]) -> bool:
    """Return ``True`` if the MCP tool descriptor advertises ``destructiveHint``.

    Per the MCP spec, ``destructiveHint=true`` indicates the tool may perform
    irreversible side-effects. The agent surfaces a confirmation request via
    ``TASK_STATE_INPUT_REQUIRED`` instead of invoking it blindly.
    """
    annotations = descriptor.get("annotations") if isinstance(descriptor, dict) else None
    if not isinstance(annotations, dict):
        return False
    return bool(annotations.get("destructiveHint"))


async def invoke_mcp_tool(
    *,
    call: dict[str, Any],
    tool_lookup: dict[str, dict[str, Any]],
    crypto: CryptoService,
    tracer: Any,
    backoffs: tuple[float, ...] = TOOL_RETRY_BACKOFFS,
) -> dict[str, Any]:
    """Invoke an MCP tool with the FR-018 3-attempt retry policy."""
    function = call.get("function") or {}
    name = function.get("name") or call.get("name") or ""
    args = parse_tool_arguments(function.get("arguments", call.get("arguments", {})))
    target = tool_lookup.get(name)
    if target is None:
        return {"is_error": True, "content": f"unknown tool: {name}"}
    server = target["server"]
    last_error: str | None = None
    for attempt, backoff in enumerate(backoffs, start=1):
        with tracer.start_as_current_span("mcp.tool_call") as span:
            span.set_attribute("tool", name)
            span.set_attribute("attempt", attempt)
            try:
                if server.transport == "stdio":
                    env = decrypt_env(server, crypto)
                    result = await call_tool_stdio(
                        command=server.command or "",
                        env=env,
                        cwd=server.cwd,
                        tool_name=name,
                        arguments=args,
                        timeout_s=float(server.tool_timeout_s or 30),
                    )
                elif server.transport == "streamable_http":
                    headers = decrypt_headers(server, crypto)
                    result = await call_tool_http(
                        url=server.url or "",
                        headers=headers,
                        tool_name=name,
                        arguments=args,
                        timeout_s=float(server.tool_timeout_s or 30),
                    )
                else:
                    return {"is_error": True, "content": f"unknown transport: {server.transport}"}
                if not result.get("is_error"):
                    return result
                last_error = str(result.get("content") or "tool_error")
            except McpToolError as exc:
                last_error = str(exc)
        if attempt < len(backoffs):
            await asyncio.sleep(backoff)
    return {"is_error": True, "content": last_error or "tool_failed"}


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


def empty_response_failure_reason(response: ChatResponse) -> str | None:
    """Return a ``stop_reason`` if the LLM response carries no usable answer.

    Called on the final LLM turn (i.e. ``response.tool_calls`` is empty) to
    detect that the provider returned nothing the user can read. Returns:

    * ``"max_tokens"`` if the provider reported ``finish_reason="length"``
      (the answer was truncated to nothing useful);
    * ``"content_filter"`` if the provider reported a content filter trip;
    * ``"empty_response"`` for any other case where ``content`` is blank;
    * ``None`` if there is something to render (i.e. content has non-whitespace).

    Reasoning-only responses (``content`` empty, ``reasoning`` non-empty) still
    fail here: reasoning is internal and must not be served as the final
    artifact. Executors are expected to surface ``response.reasoning`` as a
    thinking event before calling this check.
    """
    if (response.content or "").strip():
        return None
    finish = response.finish_reason
    if finish == "length":
        return "max_tokens"
    if finish == "content_filter":
        return "content_filter"
    return "empty_response"


def reasoning_event(emitter: A2AEventEmitter, response: ChatResponse) -> bytes | None:
    """Build a ``WORKING`` SSE chunk carrying the LLM's reasoning, or ``None``.

    Per the web-a2a FR-023 contract, internal reasoning is exposed via a
    ``TaskStatusUpdateEvent`` in ``WORKING`` state with a message tagged
    ``metadata.kind == "thought"``. Returns ``None`` when the response did
    not include reasoning so the caller can short-circuit cleanly.
    """
    text = (response.reasoning or "").strip()
    if not text:
        return None
    return emitter.working(message_text=text, metadata={"kind": "thought"})


__all__ = [
    "TOOL_RETRY_BACKOFFS",
    "AsyncIterator",
    "ExecutorContext",
    "collect_tools",
    "empty_response_failure_reason",
    "encode_decimal",
    "invoke_mcp_tool",
    "needs_confirmation",
    "parse_tool_arguments",
    "reasoning_event",
]
