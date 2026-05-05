"""Simple-mode executor: loop only on tool calls."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.executors.base import (
    ExecutorContext,
    empty_response_failure_reason,
    needs_confirmation,
    parse_tool_arguments,
    reasoning_event,
)
from lcnc_a2a.llm.provider import ChatResponse, LlmProvider, LlmProviderError
from lcnc_a2a.llm.tool_format import to_openai_tools
from lcnc_a2a.mcp_client.tool_caller import McpToolError, call_tool_http, call_tool_stdio
from lcnc_a2a.observability.otel import get_tracer
from lcnc_a2a.services import messages as messages_service
from lcnc_a2a.services import runs as runs_service
from lcnc_a2a.services.mcp_discovery import decrypt_env, decrypt_headers

MAX_ITERATIONS = 50
TOOL_RETRY_BACKOFFS = (0.2, 0.6, 1.8)
_APPROVAL_TOKENS = frozenset({"yes", "y", "ok", "oui", "o", "approve", "approved", "confirm"})

logger = logging.getLogger(__name__)


def _is_approval(text: str) -> bool:
    """Loose user-confirmation parser for INPUT_REQUIRED resume."""
    return text.strip().lower() in _APPROVAL_TOKENS


def _summarize_call(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    fn = call.get("function") or {}
    name = fn.get("name") or call.get("name") or ""
    args = parse_tool_arguments(fn.get("arguments", call.get("arguments", {})))
    return name, args


class SimpleExecutor:
    """Run a single Simple-mode agent execution and yield SSE bytes."""

    __slots__ = ("_crypto", "_db", "_provider")

    def __init__(self, *, db: AsyncSession, provider: LlmProvider, crypto: CryptoService) -> None:
        self._db = db
        self._provider = provider
        self._crypto = crypto

    async def run(self, ctx: ExecutorContext) -> AsyncIterator[bytes]:
        """Drive a Simple-mode run; yield SSE event bytes for the response stream."""
        cancelled = False
        cancel_event = ctx.cancellation
        emitter = ctx.emitter
        is_resume = ctx.resume_action is not None
        # Tracked so the ``finally`` block knows whether to write a terminal
        # row when we exit through an unhandled CancelledError or exception.
        finalized = False
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost: Decimal | None = None
        loops = 0

        if not is_resume:
            # Fresh run: persist the user's message (may raise context_full).
            try:
                await messages_service.append_message(
                    self._db,
                    context_id=ctx.context_id,
                    role="user",
                    content=ctx.user_text,
                )
            except messages_service.ContextFullError:
                await runs_service.finalize_run(
                    self._db,
                    run_id=ctx.run.id,
                    status="failed",
                    stop_reason="context_full",
                    final_answer=None,
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=None,
                    loops=0,
                )
                finalized = True
                await self._db.commit()
                yield emitter.working()
                yield emitter.failed(reason="context_full")
                return
            await self._db.commit()

        yield emitter.working()

        snapshot = ctx.run.config_snapshot or {}
        system_prompt = snapshot.get("system_prompt") if isinstance(snapshot, dict) else None
        max_tokens = (
            int(snapshot.get("max_tokens") or ctx.agent.max_tokens)
            if isinstance(snapshot, dict)
            else ctx.agent.max_tokens
        )
        model_id = snapshot.get("model_id") if isinstance(snapshot, dict) else ctx.agent.model_id
        model_endpoint = snapshot.get("model_endpoint") if isinstance(snapshot, dict) else ctx.agent.model_endpoint

        tools = self._collect_tools(ctx)
        openai_tools = to_openai_tools([tool["descriptor"] for tool in tools]) or None
        tool_lookup = {tool["descriptor"]["name"]: tool for tool in tools}

        run_seq = 0
        tracer = get_tracer()

        if is_resume:
            # Restore the snapshot the pause path persisted.
            action = ctx.resume_action or {}
            run_seq = int(action.get("run_seq") or 0)
            loops = int(action.get("loops") or 0)
            total_tokens_in = int(action.get("tokens_in") or 0)
            total_tokens_out = int(action.get("tokens_out") or 0)
            saved_calls: list[dict[str, Any]] = action.get("tool_calls") or []
            approved = _is_approval(ctx.user_text)
            for call in saved_calls:
                if cancel_event.is_set():
                    cancelled = True
                    break
                run_seq += 1
                tool_call_id = call.get("id") or ""
                tool_name, _args = _summarize_call(call)
                if approved:
                    tool_payload = await self._invoke_tool(call, tool_lookup, tracer)
                else:
                    tool_payload = {"is_error": True, "content": "user_denied"}
                await runs_service.append_run_step(
                    self._db,
                    run_id=ctx.run.id,
                    seq=run_seq,
                    role="tool",
                    content=tool_payload.get("content", ""),
                    tool_name=tool_name,
                    tool_result_json=tool_payload,
                )
                await messages_service.append_message(
                    self._db,
                    context_id=ctx.context_id,
                    role="tool",
                    content=str(tool_payload.get("content", "")),
                    tool_call_id=tool_call_id,
                )
            await runs_service.resume_run(self._db, run_id=ctx.run.id)
            await self._db.commit()

        try:
            for iteration in range(MAX_ITERATIONS + 1):
                if cancel_event.is_set():
                    cancelled = True
                    break

                if iteration == MAX_ITERATIONS:
                    await runs_service.finalize_run(
                        self._db,
                        run_id=ctx.run.id,
                        status="failed",
                        stop_reason="guardrail_exceeded",
                        final_answer=None,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        cost_usd=total_cost,
                        loops=loops,
                    )
                    finalized = True
                    await self._db.commit()
                    yield emitter.failed(reason="guardrail_exceeded")
                    return

                persisted = await messages_service.list_messages(self._db, context_id=ctx.context_id)
                payload = messages_service.build_llm_payload(persisted, system_prompt=system_prompt)

                with tracer.start_as_current_span("executor.simple.iter") as span:
                    span.set_attribute("iteration", iteration)
                    chat_start = time.perf_counter()
                    try:
                        with tracer.start_as_current_span("llm.chat") as llm_span:
                            llm_span.set_attribute("model", model_id or "")
                            llm_span.set_attribute("provider", self._provider.name)
                            response: ChatResponse = await self._provider.chat(
                                messages=payload,
                                tools=openai_tools,
                                model_id=model_id or "",
                                endpoint=model_endpoint or "",
                                api_key=ctx.provider_api_key,
                                max_tokens=max_tokens,
                            )
                            duration_ms = int((time.perf_counter() - chat_start) * 1000)
                            llm_span.set_attribute("tokens.prompt", response.tokens_in)
                            llm_span.set_attribute("tokens.completion", response.tokens_out)
                            llm_span.set_attribute("duration.ms", duration_ms)
                            if response.cost_usd is not None:
                                llm_span.set_attribute("cost.usd", float(response.cost_usd))
                            if response.request_id is not None:
                                llm_span.set_attribute("request_id", response.request_id)
                    except LlmProviderError:
                        await runs_service.finalize_run(
                            self._db,
                            run_id=ctx.run.id,
                            status="failed",
                            stop_reason="llm_provider_error",
                            final_answer=None,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            cost_usd=total_cost,
                            loops=loops,
                        )
                        finalized = True
                        await self._db.commit()
                        yield emitter.failed(reason="llm_provider_error")
                        return

                if cancel_event.is_set():
                    cancelled = True
                    break

                total_tokens_in += response.tokens_in
                total_tokens_out += response.tokens_out
                if response.cost_usd is not None:
                    total_cost = (total_cost or Decimal("0")) + response.cost_usd

                if not response.tool_calls:
                    # Surface the LLM's internal reasoning (if any) as a
                    # WORKING event tagged kind=thought (web-a2a FR-023).
                    thought_chunk = reasoning_event(emitter, response)
                    if thought_chunk is not None:
                        yield thought_chunk

                    # If the LLM returned no usable answer, fail explicitly
                    # rather than completing with an empty artifact.
                    failure_reason = empty_response_failure_reason(response)
                    if failure_reason is not None:
                        logger.warning(
                            "LLM returned empty content (run=%s finish_reason=%s "
                            "tokens_out=%d reasoning_chars=%d request_id=%s)",
                            ctx.run.id,
                            response.finish_reason,
                            response.tokens_out,
                            len(response.reasoning),
                            response.request_id,
                        )
                        await runs_service.finalize_run(
                            self._db,
                            run_id=ctx.run.id,
                            status="failed",
                            stop_reason=failure_reason,
                            final_answer=None,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            cost_usd=total_cost,
                            loops=loops,
                        )
                        finalized = True
                        await self._db.commit()
                        yield emitter.failed(reason=failure_reason)
                        return

                    final_text = response.content or ""
                    run_seq += 1
                    await runs_service.append_run_step(
                        self._db,
                        run_id=ctx.run.id,
                        seq=run_seq,
                        role="assistant",
                        content=final_text,
                        tokens_in=response.tokens_in,
                        tokens_out=response.tokens_out,
                    )
                    await messages_service.append_message(
                        self._db,
                        context_id=ctx.context_id,
                        role="assistant",
                        content=final_text,
                    )
                    loops += 1
                    await runs_service.finalize_run(
                        self._db,
                        run_id=ctx.run.id,
                        status="completed",
                        stop_reason=None,
                        final_answer=final_text,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        cost_usd=total_cost,
                        loops=loops,
                    )
                    finalized = True
                    await self._db.commit()
                    yield emitter.artifact(final_text)
                    yield emitter.completed()
                    return

                # Tool-call branch.
                run_seq += 1
                assistant_step_content = response.content or ""
                await runs_service.append_run_step(
                    self._db,
                    run_id=ctx.run.id,
                    seq=run_seq,
                    role="assistant",
                    content=assistant_step_content,
                    tool_args_json=response.tool_calls,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                )
                await messages_service.append_message(
                    self._db,
                    context_id=ctx.context_id,
                    role="assistant",
                    content=assistant_step_content,
                    tool_call_json=response.tool_calls,
                )
                loops += 1

                # Pause if any tool call carries destructiveHint=true. The
                # entire batch waits for one user confirmation; resume executes
                # all of them (or none if denied).
                destructive = self._first_destructive(response.tool_calls, tool_lookup)
                if destructive is not None:
                    name, args = _summarize_call(destructive)
                    pending = {
                        "tool_calls": response.tool_calls,
                        "run_seq": run_seq,
                        "loops": loops,
                        "tokens_in": total_tokens_in,
                        "tokens_out": total_tokens_out,
                    }
                    await runs_service.pause_run_for_input(
                        self._db,
                        run_id=ctx.run.id,
                        pending_action=pending,
                    )
                    # Pause is a terminal state for this execution path; the
                    # next resume call will move it back to ``running``.
                    finalized = True
                    await self._db.commit()
                    yield emitter.input_required(
                        f"The agent wants to call '{name}' with arguments {args}. "
                        f"Reply 'yes' to approve or 'no' to skip.",
                        metadata={
                            "kind": "confirm_tool",
                            "tool_name": name,
                            "arguments": args,
                            "tool_call_id": destructive.get("id") or "",
                        },
                    )
                    return

                for call in response.tool_calls:
                    if cancel_event.is_set():
                        cancelled = True
                        break
                    tool_payload = await self._invoke_tool(call, tool_lookup, tracer)
                    run_seq += 1
                    tool_call_id = call.get("id") or ""
                    tool_name = (call.get("function") or {}).get("name") or call.get("name") or ""
                    await runs_service.append_run_step(
                        self._db,
                        run_id=ctx.run.id,
                        seq=run_seq,
                        role="tool",
                        content=tool_payload.get("content", ""),
                        tool_name=tool_name,
                        tool_result_json=tool_payload,
                    )
                    await messages_service.append_message(
                        self._db,
                        context_id=ctx.context_id,
                        role="tool",
                        content=str(tool_payload.get("content", "")),
                        tool_call_id=tool_call_id,
                    )
                await self._db.commit()

                if total_tokens_out >= max_tokens:
                    await runs_service.finalize_run(
                        self._db,
                        run_id=ctx.run.id,
                        status="failed",
                        stop_reason="max_tokens",
                        final_answer=None,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        cost_usd=total_cost,
                        loops=loops,
                    )
                    finalized = True
                    await self._db.commit()
                    yield emitter.failed(reason="max_tokens")
                    return
        finally:
            # Last-resort finalize: covers client disconnect (CancelledError /
            # GeneratorExit) and unexpected exceptions. Inline finalize paths
            # already set ``finalized=True``; here we close the gap so a run
            # never stays ``running`` forever in the DB.
            if not finalized:
                exc_type = sys.exc_info()[0]
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        runs_service.finalize_orphan_run(
                            self._db,
                            run_id=ctx.run.id,
                            cancel_event_set=cancel_event.is_set(),
                            exc_type=exc_type,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            cost_usd=total_cost,
                            loops=loops,
                        )
                    )

        if cancelled:
            yield emitter.canceled()

    def _first_destructive(
        self,
        tool_calls: list[dict[str, Any]],
        tool_lookup: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Return the first tool call whose descriptor has ``destructiveHint=true``."""
        for call in tool_calls:
            name, _ = _summarize_call(call)
            target = tool_lookup.get(name)
            if target is None:
                continue
            if needs_confirmation(target.get("descriptor") or {}):
                return call
        return None

    def _collect_tools(self, ctx: ExecutorContext) -> list[dict[str, Any]]:
        """Flatten ``tools_cache`` across all attached MCP servers."""
        out: list[dict[str, Any]] = []
        for server in ctx.mcp_servers:
            cache = server.tools_cache or {}
            tools = cache.get("tools") if isinstance(cache, dict) else None
            if not isinstance(tools, list):
                continue
            for descriptor in tools:
                if not isinstance(descriptor, dict):
                    continue
                out.append({"server": server, "descriptor": descriptor})
        return out

    async def _invoke_tool(
        self,
        call: dict[str, Any],
        tool_lookup: dict[str, dict[str, Any]],
        tracer: Any,
    ) -> dict[str, Any]:
        function = call.get("function") or {}
        name = function.get("name") or call.get("name") or ""
        args = parse_tool_arguments(function.get("arguments", call.get("arguments", {})))
        target = tool_lookup.get(name)
        if target is None:
            return {"is_error": True, "content": f"unknown tool: {name}"}
        server = target["server"]
        last_error: str | None = None
        for attempt, backoff in enumerate(TOOL_RETRY_BACKOFFS, start=1):
            with tracer.start_as_current_span("mcp.tool_call") as span:
                span.set_attribute("tool", name)
                span.set_attribute("attempt", attempt)
                try:
                    if server.transport == "stdio":
                        env = decrypt_env(server, self._crypto)
                        result = await call_tool_stdio(
                            command=server.command or "",
                            env=env,
                            cwd=server.cwd,
                            tool_name=name,
                            arguments=args,
                            timeout_s=float(server.tool_timeout_s or 30),
                        )
                    elif server.transport == "streamable_http":
                        headers = decrypt_headers(server, self._crypto)
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
            if attempt < len(TOOL_RETRY_BACKOFFS):
                await asyncio.sleep(backoff)
        return {"is_error": True, "content": last_error or "tool_failed"}


__all__ = ["MAX_ITERATIONS", "SimpleExecutor"]
