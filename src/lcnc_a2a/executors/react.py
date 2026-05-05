"""ReAct-mode executor: Thought / Action / Observation loop (FR-015, FR-017, FR-019)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.executors.base import (
    ExecutorContext,
    collect_tools,
    invoke_mcp_tool,
)
from lcnc_a2a.executors.synthesis import (
    run_synthesis,
    should_skip_synthesis,
)
from lcnc_a2a.llm.embeddings import (
    EmbeddingError,
    EmbeddingResult,
    embed,
    resolve_embedding_model,
)
from lcnc_a2a.llm.provider import ChatResponse, LlmProvider, LlmProviderError
from lcnc_a2a.llm.tool_format import to_openai_tools
from lcnc_a2a.models.agent_run_step import AgentRunStep
from lcnc_a2a.observability.otel import get_tracer
from lcnc_a2a.services import messages as messages_service
from lcnc_a2a.services import runs as runs_service
from lcnc_a2a.services.similarity import cosine_similarity

REACT_DEFAULT_SIMILARITY_THRESHOLD = 0.95
FINAL_ANSWER_PREFIX = "Final Answer:"


@dataclass
class _IterOutcome:
    """The parsed outcome of one ReAct iteration's LLM response."""

    kind: str  # "tool_call" | "final" | "parse_error"
    thought: str = ""
    final_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


def parse_react_response(response: ChatResponse) -> _IterOutcome:
    """Classify an LLM response into tool-call / final-answer / parse-error.

    Contract: the prompt instructs the model to either emit one or more
    OpenAI tool calls (with optional ``Thought:`` content) or to reply with
    content prefixed by ``Final Answer:``. Anything else is a parse error.
    """
    if response.tool_calls:
        thought = (response.content or "").strip()
        if thought.startswith("Thought:"):
            thought = thought[len("Thought:") :].strip()
        return _IterOutcome(kind="tool_call", thought=thought, tool_calls=list(response.tool_calls))
    content = (response.content or "").strip()
    if not content:
        return _IterOutcome(kind="parse_error")
    if content.startswith(FINAL_ANSWER_PREFIX):
        final = content[len(FINAL_ANSWER_PREFIX) :].strip()
        return _IterOutcome(kind="final", final_text=final)
    return _IterOutcome(kind="parse_error")


class ReActExecutor:
    """Drive a single ReAct-mode A2A run."""

    __slots__ = ("_crypto", "_db", "_embedding_client", "_provider")

    def __init__(
        self,
        *,
        db: AsyncSession,
        provider: LlmProvider,
        crypto: CryptoService,
        embedding_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._db = db
        self._provider = provider
        self._crypto = crypto
        self._embedding_client = embedding_client

    async def run(self, ctx: ExecutorContext) -> AsyncIterator[bytes]:
        """Execute the ReAct loop and yield SSE event bytes."""
        cancel_event = ctx.cancellation
        cancelled = False
        emitter = ctx.emitter

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
            await self._db.commit()
            yield emitter.working()
            yield emitter.failed(reason="context_full")
            return
        await self._db.commit()

        yield emitter.working()

        snapshot = ctx.run.config_snapshot if isinstance(ctx.run.config_snapshot, dict) else {}
        system_prompt = snapshot.get("system_prompt") or None
        max_loops = int(snapshot.get("max_loops") or ctx.agent.max_loops)
        max_tokens = int(snapshot.get("max_tokens") or ctx.agent.max_tokens)
        similarity_threshold = float(
            snapshot.get("similarity_threshold") or ctx.agent.similarity_threshold or REACT_DEFAULT_SIMILARITY_THRESHOLD
        )
        model_id = snapshot.get("model_id") or ctx.agent.model_id
        model_endpoint = snapshot.get("model_endpoint") or ctx.agent.model_endpoint
        model_provider_name = snapshot.get("model_provider") or ctx.agent.model_provider
        embedding_model = resolve_embedding_model(
            provider=model_provider_name,
            agent_embedding_model=snapshot.get("embedding_model") or ctx.agent.embedding_model,
        )

        tools = collect_tools(ctx.mcp_servers)
        openai_tools = to_openai_tools([t["descriptor"] for t in tools]) or None
        tool_lookup = {t["descriptor"]["name"]: t for t in tools}

        tracer = get_tracer()
        run_seq = 0
        loops = 0
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost: Decimal | None = None
        scratchpad: list[str] = []
        prev_candidate_text: str | None = None
        prev_vector: list[float] | None = None
        stop_reason_pending: str | None = None

        try:
            while True:
                if cancel_event.is_set():
                    cancelled = True
                    break

                loop_index = loops + 1

                persisted = await messages_service.list_messages(self._db, context_id=ctx.context_id)
                payload = self._build_react_payload(
                    persisted=persisted,
                    system_prompt=system_prompt,
                    scratchpad=scratchpad,
                )

                response: ChatResponse
                try:
                    with tracer.start_as_current_span("executor.react.iter") as span:
                        span.set_attribute("iteration", loop_index)
                        chat_start = time.perf_counter()
                        with tracer.start_as_current_span("llm.chat") as llm_span:
                            llm_span.set_attribute("model", model_id or "")
                            llm_span.set_attribute("provider", self._provider.name)
                            response = await self._provider.chat(
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
                    await self._db.commit()
                    yield emitter.failed(reason="llm_provider_error")
                    return

                total_tokens_in += response.tokens_in
                total_tokens_out += response.tokens_out
                if response.cost_usd is not None:
                    total_cost = (total_cost or Decimal("0")) + response.cost_usd

                outcome = parse_react_response(response)
                loops += 1

                if outcome.kind == "parse_error":
                    run_seq += 1
                    await runs_service.append_run_step(
                        self._db,
                        run_id=ctx.run.id,
                        seq=run_seq,
                        role="error",
                        content="parse_error",
                        tokens_in=response.tokens_in,
                        tokens_out=response.tokens_out,
                    )
                    await self._db.commit()
                    if loops >= max_loops or total_tokens_out >= max_tokens:
                        stop_reason_pending = "max_loops" if loops >= max_loops else "max_tokens"
                        break
                    continue

                if outcome.kind == "final":
                    final_text = outcome.final_text
                    run_seq += 1
                    await runs_service.append_run_step(
                        self._db,
                        run_id=ctx.run.id,
                        seq=run_seq,
                        role="thought",
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
                    await runs_service.finalize_run(
                        self._db,
                        run_id=ctx.run.id,
                        status="completed",
                        stop_reason="final",
                        final_answer=final_text,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        cost_usd=total_cost,
                        loops=loops,
                    )
                    await self._db.commit()
                    yield emitter.artifact(final_text)
                    yield emitter.completed()
                    return

                # tool_call branch
                candidate_text = outcome.thought
                yield emitter.working(metadata={"loop": loop_index, "phase": "thought"})
                yield emitter.working(metadata={"loop": loop_index, "phase": "action"})

                # Similarity check fires only from iter 2 onwards.
                similarity_value: float | None = None
                if loop_index >= 2 and prev_candidate_text is not None:
                    try:
                        if prev_vector is None:
                            prev_embed = await self._embed_text(
                                text=prev_candidate_text,
                                model=embedding_model,
                                endpoint=model_endpoint or "",
                                api_key=ctx.provider_api_key,
                                provider_name=model_provider_name,
                                tracer=tracer,
                            )
                            prev_vector = prev_embed.vector
                            total_tokens_in += prev_embed.tokens_in
                            if prev_embed.cost_usd is not None:
                                total_cost = (total_cost or Decimal("0")) + prev_embed.cost_usd
                        current_embed = await self._embed_text(
                            text=candidate_text,
                            model=embedding_model,
                            endpoint=model_endpoint or "",
                            api_key=ctx.provider_api_key,
                            provider_name=model_provider_name,
                            tracer=tracer,
                        )
                        total_tokens_in += current_embed.tokens_in
                        if current_embed.cost_usd is not None:
                            total_cost = (total_cost or Decimal("0")) + current_embed.cost_usd
                        similarity_value = cosine_similarity(current_embed.vector, prev_vector)
                        prev_vector = current_embed.vector
                    except EmbeddingError:
                        await runs_service.finalize_run(
                            self._db,
                            run_id=ctx.run.id,
                            status="failed",
                            stop_reason="embedding_unavailable",
                            final_answer=None,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            cost_usd=total_cost,
                            loops=loops,
                        )
                        await self._db.commit()
                        yield emitter.failed(reason="embedding_unavailable")
                        return

                run_seq += 1
                await runs_service.append_run_step(
                    self._db,
                    run_id=ctx.run.id,
                    seq=run_seq,
                    role="thought",
                    content=candidate_text,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                )
                if similarity_value is not None:
                    await self._db.execute(
                        update(AgentRunStep)
                        .where(AgentRunStep.run_id == ctx.run.id, AgentRunStep.seq == run_seq)
                        .values(similarity_to_prev=similarity_value)
                    )

                run_seq += 1
                await runs_service.append_run_step(
                    self._db,
                    run_id=ctx.run.id,
                    seq=run_seq,
                    role="action",
                    content=None,
                    tool_args_json=outcome.tool_calls,
                )

                scratchpad.append(f"Thought: {candidate_text}")

                # Stop by similarity → return PREVIOUS candidate.
                if similarity_value is not None and similarity_value >= similarity_threshold:
                    final_text = prev_candidate_text or ""
                    await messages_service.append_message(
                        self._db,
                        context_id=ctx.context_id,
                        role="assistant",
                        content=final_text,
                    )
                    await runs_service.finalize_run(
                        self._db,
                        run_id=ctx.run.id,
                        status="completed",
                        stop_reason="similarity",
                        final_answer=final_text,
                        tokens_in=total_tokens_in,
                        tokens_out=total_tokens_out,
                        cost_usd=total_cost,
                        loops=loops,
                    )
                    await self._db.commit()
                    yield emitter.artifact(final_text)
                    yield emitter.completed()
                    return

                # Run tool calls (with FR-018 retry).
                for call in outcome.tool_calls:
                    if cancel_event.is_set():
                        cancelled = True
                        break
                    tool_payload = await invoke_mcp_tool(
                        call=call,
                        tool_lookup=tool_lookup,
                        crypto=self._crypto,
                        tracer=tracer,
                    )
                    run_seq += 1
                    tool_name = (call.get("function") or {}).get("name") or call.get("name") or ""
                    await runs_service.append_run_step(
                        self._db,
                        run_id=ctx.run.id,
                        seq=run_seq,
                        role="observation",
                        content=str(tool_payload.get("content", "")),
                        tool_name=tool_name,
                        tool_result_json=tool_payload,
                    )
                    args_json = (call.get("function") or {}).get("arguments", "")
                    scratchpad.append(f"Action: {tool_name}({args_json})")
                    scratchpad.append(f"Observation: {tool_payload.get('content', '')}")

                if cancelled:
                    break

                yield emitter.working(metadata={"loop": loop_index, "phase": "observation"})
                await self._db.commit()

                prev_candidate_text = candidate_text

                if loops >= max_loops or total_tokens_out >= max_tokens:
                    stop_reason_pending = "max_loops" if loops >= max_loops else "max_tokens"
                    break
        finally:
            if cancelled:
                await runs_service.finalize_run(
                    self._db,
                    run_id=ctx.run.id,
                    status="cancelled",
                    stop_reason="cancelled",
                    final_answer=None,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    cost_usd=total_cost,
                    loops=loops,
                )
                try:
                    await self._db.commit()
                except Exception:
                    await self._db.rollback()

        if cancelled:
            yield emitter.canceled()
            return

        if stop_reason_pending is not None:
            scratchpad_text = "\n".join(scratchpad)
            scratchpad_chars = len(scratchpad_text)
            if should_skip_synthesis(
                cumulative_tokens=total_tokens_out,
                max_tokens=max_tokens,
                scratchpad_chars=scratchpad_chars,
            ):
                await runs_service.finalize_run(
                    self._db,
                    run_id=ctx.run.id,
                    status="failed",
                    stop_reason="guardrail_exceeded_no_synthesis",
                    final_answer=None,
                    tokens_in=total_tokens_in,
                    tokens_out=total_tokens_out,
                    cost_usd=total_cost,
                    loops=loops,
                )
                await self._db.commit()
                yield emitter.failed(reason="guardrail_exceeded_no_synthesis")
                return

            try:
                synth_response = await run_synthesis(
                    provider=self._provider,
                    system_prompt=system_prompt,
                    user_text=ctx.user_text,
                    scratchpad_text=scratchpad_text,
                    model_id=model_id or "",
                    endpoint=model_endpoint or "",
                    api_key=ctx.provider_api_key,
                    max_tokens=max_tokens,
                )
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
                await self._db.commit()
                yield emitter.failed(reason="llm_provider_error")
                return

            total_tokens_in += synth_response.tokens_in
            total_tokens_out += synth_response.tokens_out
            if synth_response.cost_usd is not None:
                total_cost = (total_cost or Decimal("0")) + synth_response.cost_usd

            final_text = synth_response.content or ""
            run_seq += 1
            await runs_service.append_run_step(
                self._db,
                run_id=ctx.run.id,
                seq=run_seq,
                role="synthesis",
                content=final_text,
                tokens_in=synth_response.tokens_in,
                tokens_out=synth_response.tokens_out,
            )
            await messages_service.append_message(
                self._db,
                context_id=ctx.context_id,
                role="assistant",
                content=final_text,
            )
            await runs_service.finalize_run(
                self._db,
                run_id=ctx.run.id,
                status="completed",
                stop_reason=stop_reason_pending,
                final_answer=final_text,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                cost_usd=total_cost,
                loops=loops,
            )
            await self._db.commit()
            yield emitter.artifact(final_text)
            yield emitter.completed()
            return

    async def _embed_text(
        self,
        *,
        text: str,
        model: str,
        endpoint: str,
        api_key: str,
        provider_name: str,
        tracer: Any,
    ) -> EmbeddingResult:
        with tracer.start_as_current_span("llm.embed") as span:
            span.set_attribute("model", model)
            span.set_attribute("provider", provider_name)
            start = time.perf_counter()
            include_cost = provider_name == "openrouter"
            result = await embed(
                text=text,
                model=model,
                endpoint=endpoint,
                api_key=api_key,
                include_cost=include_cost,
                client=self._embedding_client,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            span.set_attribute("tokens.prompt", result.tokens_in)
            span.set_attribute("tokens.completion", result.tokens_out)
            span.set_attribute("duration.ms", duration_ms)
            if result.cost_usd is not None:
                span.set_attribute("cost.usd", float(result.cost_usd))
            if result.request_id is not None:
                span.set_attribute("request_id", result.request_id)
            return result

    @staticmethod
    def _build_react_payload(
        *,
        persisted: list[Any],
        system_prompt: str | None,
        scratchpad: list[str],
    ) -> list[dict[str, Any]]:
        """Build the OpenAI ``messages`` payload for the next ReAct iter."""
        payload = messages_service.build_llm_payload(persisted, system_prompt=system_prompt)
        if scratchpad:
            payload.append({"role": "user", "content": "\n".join(scratchpad)})
        return payload


__all__ = ["FINAL_ANSWER_PREFIX", "ReActExecutor", "parse_react_response"]
