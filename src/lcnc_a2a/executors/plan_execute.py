"""Plan & Execute executor: planner pass + stage-parallel executor + synthesis.

See ``specs/user_stories/US-007_plan_execute_executor.md`` for the contract.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.a2a.envelope import task_artifact_update, task_status_update
from lcnc_a2a.a2a.sse import encode_sse_event
from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.executors.base import (
    ExecutorContext,
    collect_tools,
    invoke_mcp_tool,
)
from lcnc_a2a.executors.synthesis import (
    SYNTHESIS_TEMPLATE,
    should_skip_synthesis,
)
from lcnc_a2a.llm.provider import ChatResponse, LlmProvider, LlmProviderError
from lcnc_a2a.models.agent_run_step import AgentRunStep
from lcnc_a2a.observability.otel import get_tracer
from lcnc_a2a.services import messages as messages_service
from lcnc_a2a.services import runs as runs_service
from lcnc_a2a.services.plan_substitution import substitute_args
from lcnc_a2a.services.plan_validator import (
    Plan,
    PlanStep,
    PlanValidationError,
    parse_and_validate_plan,
)

DEFAULT_MAX_STEPS = 20
MAX_REPLANS = 3
MAX_PLANNER_RETRIES = 1  # one retry after a validation error before failing the run

SYNTHESIZE_TOOL = "synthesize"


@dataclass(frozen=True, slots=True)
class _StepOutcome:
    """Result of running a single PE step."""

    step: PlanStep
    status: str  # "success" | "failure" | "replan_requested"
    output: str
    notes: str
    reason: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal | None
    tool_args: dict[str, Any]
    tool_result: dict[str, Any] | None


class PlanExecuteExecutor:
    """Drive a single Plan & Execute A2A run."""

    __slots__ = ("_crypto", "_db", "_provider")

    def __init__(self, *, db: AsyncSession, provider: LlmProvider, crypto: CryptoService) -> None:
        self._db = db
        self._provider = provider
        self._crypto = crypto

    async def run(self, ctx: ExecutorContext) -> AsyncIterator[bytes]:
        """Execute the PE pipeline and yield SSE event bytes."""
        cancel_event = ctx.cancellation
        cancelled = False

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
            yield encode_sse_event(task_status_update("working"))
            yield encode_sse_event(task_status_update("failed", reason="context_full"))
            return
        await self._db.commit()

        yield encode_sse_event(task_status_update("working"))

        snapshot = ctx.run.config_snapshot if isinstance(ctx.run.config_snapshot, dict) else {}
        planner_prompt = snapshot.get("planner_prompt") or ctx.agent.planner_prompt or ""
        executor_prompt = snapshot.get("executor_prompt") or ctx.agent.executor_prompt or ""
        max_steps = int(snapshot.get("max_steps") or ctx.agent.max_steps or DEFAULT_MAX_STEPS)
        max_tokens = int(snapshot.get("max_tokens") or ctx.agent.max_tokens)
        model_id = snapshot.get("model_id") or ctx.agent.model_id
        model_endpoint = snapshot.get("model_endpoint") or ctx.agent.model_endpoint

        tools = collect_tools(ctx.mcp_servers)
        tool_lookup = {t["descriptor"]["name"]: t for t in tools}
        available_tools = set(tool_lookup.keys())

        tracer = get_tracer()
        run_seq = 0
        loops = 0  # PE loops field is reserved for synthesis defensive cap (0 or 1).
        total_tokens_in = 0
        total_tokens_out = 0
        total_cost: Decimal | None = None

        # Track step outcomes across replans.
        step_outputs: dict[int, str] = {}
        completed_step_ids: set[int] = set()

        # SSE: planning phase.
        yield encode_sse_event(task_status_update("working", payload={"phase": "planning"}))

        # ---- Initial planner call (with one validation retry) ----
        try:
            initial_plan, initial_plan_payload, planner_tokens = await self._call_planner(
                ctx=ctx,
                planner_prompt=planner_prompt,
                max_steps=max_steps,
                max_tokens=max_tokens,
                available_tools=available_tools,
                model_id=model_id or "",
                model_endpoint=model_endpoint or "",
                completed_outputs={},
                replan_reason=None,
                tracer=tracer,
            )
        except _PlannerFailed as exc:
            total_tokens_in += exc.tokens_in
            total_tokens_out += exc.tokens_out
            if exc.cost_usd is not None:
                total_cost = (total_cost or Decimal("0")) + exc.cost_usd
            await runs_service.finalize_run(
                self._db,
                run_id=ctx.run.id,
                status="failed",
                stop_reason=exc.stop_reason,
                final_answer=None,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                cost_usd=total_cost,
                loops=loops,
            )
            await self._db.commit()
            yield encode_sse_event(task_status_update("failed", reason=exc.stop_reason))
            return

        total_tokens_in += planner_tokens.tokens_in
        total_tokens_out += planner_tokens.tokens_out
        if planner_tokens.cost_usd is not None:
            total_cost = (total_cost or Decimal("0")) + planner_tokens.cost_usd

        # Persist initial plan and stamp it onto the run row.
        run_seq += 1
        await runs_service.append_run_step(
            self._db,
            run_id=ctx.run.id,
            seq=run_seq,
            role="plan",
            content=initial_plan.goal,
            tool_args_json=initial_plan_payload,
            tokens_in=planner_tokens.tokens_in,
            tokens_out=planner_tokens.tokens_out,
        )
        ctx.run.plan = initial_plan_payload
        await self._db.flush()
        await self._db.commit()

        plan: Plan = initial_plan
        replan_count = 0

        max_tokens_reached = False
        step_failed = False

        try:
            while True:
                if cancel_event.is_set():
                    cancelled = True
                    break

                remaining = [s for s in plan.steps if s.id not in completed_step_ids]
                if not remaining:
                    break

                stage_groups = _group_by_stage(remaining)
                triggered_replan = False
                replan_reason = ""

                for stage_num, stage_steps in stage_groups:
                    if cancel_event.is_set():
                        cancelled = True
                        break

                    yield encode_sse_event(
                        task_status_update(
                            "working",
                            payload={
                                "phase": "executing",
                                "stage": stage_num,
                                "steps": [s.id for s in stage_steps],
                            },
                        )
                    )

                    coros = [
                        self._run_step(
                            ctx,
                            step,
                            step_outputs,
                            tool_lookup,
                            executor_prompt,
                            model_id or "",
                            model_endpoint or "",
                            max_tokens,
                            tracer,
                        )
                        for step in stage_steps
                    ]
                    outcomes = await asyncio.gather(*coros, return_exceptions=False)

                    # Persist results (sequentially in step.id order for stability).
                    sorted_outcomes = sorted(outcomes, key=lambda o: o.step.id)
                    for outcome in sorted_outcomes:
                        total_tokens_in += outcome.tokens_in
                        total_tokens_out += outcome.tokens_out
                        if outcome.cost_usd is not None:
                            total_cost = (total_cost or Decimal("0")) + outcome.cost_usd
                        run_seq += 1
                        await runs_service.append_run_step(
                            self._db,
                            run_id=ctx.run.id,
                            seq=run_seq,
                            role="step_result",
                            content=outcome.output,
                            tool_name=outcome.step.tool,
                            tool_args_json=outcome.tool_args,
                            tool_result_json=outcome.tool_result,
                            tokens_in=outcome.tokens_in,
                            tokens_out=outcome.tokens_out,
                        )
                        await self._db.execute(
                            sql_update(AgentRunStep)
                            .where(AgentRunStep.run_id == ctx.run.id, AgentRunStep.seq == run_seq)
                            .values(
                                stage=outcome.step.stage,
                                step_id=outcome.step.id,
                                step_status=outcome.status,
                            )
                        )
                    await self._db.commit()

                    # Check stage-level outcomes.
                    failure = next((o for o in sorted_outcomes if o.status == "failure"), None)
                    if failure is not None:
                        step_failed = True
                        break

                    replan_request = next((o for o in sorted_outcomes if o.status == "replan_requested"), None)
                    if replan_request is not None:
                        # Mark sibling success steps as completed so they survive the replan.
                        for o in sorted_outcomes:
                            if o.status == "success":
                                completed_step_ids.add(o.step.id)
                                step_outputs[o.step.id] = o.output
                        triggered_replan = True
                        replan_reason = replan_request.reason or "replan_requested"
                        break

                    # All success — propagate outputs.
                    for o in sorted_outcomes:
                        completed_step_ids.add(o.step.id)
                        step_outputs[o.step.id] = o.output

                    if total_tokens_out >= max_tokens:
                        max_tokens_reached = True
                        break

                if cancelled or step_failed or max_tokens_reached:
                    break

                if triggered_replan:
                    if replan_count >= MAX_REPLANS:
                        await runs_service.finalize_run(
                            self._db,
                            run_id=ctx.run.id,
                            status="failed",
                            stop_reason="replan_exceeded",
                            final_answer=None,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            cost_usd=total_cost,
                            loops=loops,
                        )
                        await self._db.commit()
                        yield encode_sse_event(task_status_update("failed", reason="replan_exceeded"))
                        return

                    replan_count += 1
                    yield encode_sse_event(task_status_update("working", payload={"phase": "planning"}))
                    try:
                        new_plan, new_plan_payload, planner_tokens = await self._call_planner(
                            ctx=ctx,
                            planner_prompt=planner_prompt,
                            max_steps=max_steps,
                            max_tokens=max_tokens,
                            available_tools=available_tools,
                            model_id=model_id or "",
                            model_endpoint=model_endpoint or "",
                            completed_outputs=dict(step_outputs),
                            replan_reason=replan_reason,
                            tracer=tracer,
                        )
                    except _PlannerFailed as exc:
                        total_tokens_in += exc.tokens_in
                        total_tokens_out += exc.tokens_out
                        if exc.cost_usd is not None:
                            total_cost = (total_cost or Decimal("0")) + exc.cost_usd
                        await runs_service.finalize_run(
                            self._db,
                            run_id=ctx.run.id,
                            status="failed",
                            stop_reason=exc.stop_reason,
                            final_answer=None,
                            tokens_in=total_tokens_in,
                            tokens_out=total_tokens_out,
                            cost_usd=total_cost,
                            loops=loops,
                        )
                        await self._db.commit()
                        yield encode_sse_event(task_status_update("failed", reason=exc.stop_reason))
                        return

                    total_tokens_in += planner_tokens.tokens_in
                    total_tokens_out += planner_tokens.tokens_out
                    if planner_tokens.cost_usd is not None:
                        total_cost = (total_cost or Decimal("0")) + planner_tokens.cost_usd

                    run_seq += 1
                    await runs_service.append_run_step(
                        self._db,
                        run_id=ctx.run.id,
                        seq=run_seq,
                        role="plan",
                        content=new_plan.goal,
                        tool_args_json=new_plan_payload,
                        tokens_in=planner_tokens.tokens_in,
                        tokens_out=planner_tokens.tokens_out,
                    )
                    await self._db.commit()
                    plan = new_plan
                    continue

                # No replan, no failure, no max-tokens → all stages done.
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
            yield encode_sse_event(task_status_update("cancelled"))
            return

        if step_failed:
            await runs_service.finalize_run(
                self._db,
                run_id=ctx.run.id,
                status="failed",
                stop_reason="step_failed",
                final_answer=None,
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                cost_usd=total_cost,
                loops=loops,
            )
            await self._db.commit()
            yield encode_sse_event(task_status_update("failed", reason="step_failed"))
            return

        # ---- Synthesis ----
        scratchpad_text = _format_step_outputs(step_outputs)
        if should_skip_synthesis(
            cumulative_tokens=total_tokens_out,
            max_tokens=max_tokens,
            scratchpad_chars=len(scratchpad_text),
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
            yield encode_sse_event(task_status_update("failed", reason="guardrail_exceeded_no_synthesis"))
            return

        yield encode_sse_event(task_status_update("working", payload={"phase": "synthesizing"}))
        try:
            synth_response = await self._call_synthesis(
                user_text=ctx.user_text,
                scratchpad_text=scratchpad_text,
                model_id=model_id or "",
                endpoint=model_endpoint or "",
                api_key=ctx.provider_api_key,
                max_tokens=max_tokens,
                tracer=tracer,
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
            yield encode_sse_event(task_status_update("failed", reason="llm_provider_error"))
            return

        total_tokens_in += synth_response.tokens_in
        total_tokens_out += synth_response.tokens_out
        if synth_response.cost_usd is not None:
            total_cost = (total_cost or Decimal("0")) + synth_response.cost_usd
        loops = 1

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
        stop_reason = "max_tokens" if max_tokens_reached else None
        await runs_service.finalize_run(
            self._db,
            run_id=ctx.run.id,
            status="completed",
            stop_reason=stop_reason,
            final_answer=final_text,
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            cost_usd=total_cost,
            loops=loops,
        )
        await self._db.commit()
        yield encode_sse_event(task_artifact_update(final_text))
        yield encode_sse_event(task_status_update("completed"))

    async def _call_planner(
        self,
        *,
        ctx: ExecutorContext,
        planner_prompt: str,
        max_steps: int,
        max_tokens: int,
        available_tools: set[str],
        model_id: str,
        model_endpoint: str,
        completed_outputs: dict[int, str],
        replan_reason: str | None,
        tracer: Any,
    ) -> tuple[Plan, dict[str, Any], _TokenUsage]:
        """Call the planner LLM with at most one validation retry."""
        agg = _TokenUsage(0, 0, None)
        last_error: str | None = None
        for attempt in range(MAX_PLANNER_RETRIES + 1):
            messages = _build_planner_messages(
                planner_prompt=planner_prompt,
                user_text=ctx.user_text,
                tools=sorted(available_tools),
                max_steps=max_steps,
                completed_outputs=completed_outputs,
                replan_reason=replan_reason,
                validation_error=last_error,
            )
            try:
                with tracer.start_as_current_span("executor.plan_execute.planner") as span:
                    span.set_attribute("attempt", attempt)
                    chat_start = time.perf_counter()
                    response = await self._provider.chat(
                        messages=messages,
                        tools=None,
                        model_id=model_id,
                        endpoint=model_endpoint,
                        api_key=ctx.provider_api_key,
                        max_tokens=max_tokens,
                    )
                    span.set_attribute("duration.ms", int((time.perf_counter() - chat_start) * 1000))
                    span.set_attribute("tokens.prompt", response.tokens_in)
                    span.set_attribute("tokens.completion", response.tokens_out)
            except LlmProviderError as exc:
                raise _PlannerFailed(
                    stop_reason="llm_provider_error",
                    tokens_in=agg.tokens_in,
                    tokens_out=agg.tokens_out,
                    cost_usd=agg.cost_usd,
                ) from exc

            agg = agg.add(response)
            try:
                plan, payload = parse_and_validate_plan(
                    response.content,
                    max_steps=max_steps,
                    available_tools=available_tools,
                )
                return plan, payload, agg
            except PlanValidationError as exc:
                last_error = str(exc)
                continue

        raise _PlannerFailed(
            stop_reason="planning_failed",
            tokens_in=agg.tokens_in,
            tokens_out=agg.tokens_out,
            cost_usd=agg.cost_usd,
        )

    async def _run_step(
        self,
        ctx: ExecutorContext,
        step: PlanStep,
        step_outputs: dict[int, str],
        tool_lookup: dict[str, dict[str, Any]],
        executor_prompt: str,
        model_id: str,
        model_endpoint: str,
        max_tokens: int,
        tracer: Any,
    ) -> _StepOutcome:
        """Execute one PE step: substitute args, call MCP tool, ask LLM to evaluate."""
        resolved = substitute_args(step.args, step_outputs)
        tool_result: dict[str, Any] | None = None
        if step.tool != SYNTHESIZE_TOOL:
            tool_call = {
                "function": {
                    "name": step.tool,
                    "arguments": json.dumps(resolved),
                }
            }
            tool_result = await invoke_mcp_tool(
                call=tool_call,
                tool_lookup=tool_lookup,
                crypto=self._crypto,
                tracer=tracer,
            )

        messages = _build_executor_messages(
            executor_prompt=executor_prompt,
            user_text=ctx.user_text,
            step=step,
            resolved_args=resolved,
            tool_result=tool_result,
        )
        try:
            with tracer.start_as_current_span("executor.plan_execute.step") as span:
                span.set_attribute("step.id", step.id)
                span.set_attribute("step.stage", step.stage)
                span.set_attribute("step.tool", step.tool)
                response = await self._provider.chat(
                    messages=messages,
                    tools=None,
                    model_id=model_id,
                    endpoint=model_endpoint,
                    api_key=ctx.provider_api_key,
                    max_tokens=max_tokens,
                )
                span.set_attribute("tokens.prompt", response.tokens_in)
                span.set_attribute("tokens.completion", response.tokens_out)
        except LlmProviderError as exc:
            return _StepOutcome(
                step=step,
                status="failure",
                output="",
                notes="",
                reason=f"llm_provider_error:{exc}",
                tokens_in=0,
                tokens_out=0,
                cost_usd=None,
                tool_args=resolved,
                tool_result=tool_result,
            )

        parsed = _parse_step_response(response.content)
        return _StepOutcome(
            step=step,
            status=parsed["status"],
            output=parsed["output"],
            notes=parsed["notes"],
            reason=parsed["reason"],
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            cost_usd=response.cost_usd,
            tool_args=resolved,
            tool_result=tool_result,
        )

    async def _call_synthesis(
        self,
        *,
        user_text: str,
        scratchpad_text: str,
        model_id: str,
        endpoint: str,
        api_key: str,
        max_tokens: int,
        tracer: Any,
    ) -> ChatResponse:
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_text},
            {
                "role": "user",
                "content": SYNTHESIS_TEMPLATE + "\n\nStep outputs:\n" + scratchpad_text,
            },
        ]
        with tracer.start_as_current_span("executor.plan_execute.synthesis") as span:
            chat_start = time.perf_counter()
            response = await self._provider.chat(
                messages=messages,
                tools=None,
                model_id=model_id,
                endpoint=endpoint,
                api_key=api_key,
                max_tokens=max_tokens,
            )
            span.set_attribute("duration.ms", int((time.perf_counter() - chat_start) * 1000))
            span.set_attribute("tokens.prompt", response.tokens_in)
            span.set_attribute("tokens.completion", response.tokens_out)
        return response


@dataclass(frozen=True, slots=True)
class _TokenUsage:
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal | None

    def add(self, response: ChatResponse) -> _TokenUsage:
        cost = self.cost_usd
        if response.cost_usd is not None:
            cost = (cost or Decimal("0")) + response.cost_usd
        return _TokenUsage(
            tokens_in=self.tokens_in + response.tokens_in,
            tokens_out=self.tokens_out + response.tokens_out,
            cost_usd=cost,
        )


class _PlannerFailed(Exception):
    """Internal: planner pipeline failed; caller drains aggregated tokens."""

    def __init__(
        self,
        *,
        stop_reason: str,
        tokens_in: int,
        tokens_out: int,
        cost_usd: Decimal | None,
    ) -> None:
        super().__init__(stop_reason)
        self.stop_reason = stop_reason
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.cost_usd = cost_usd


def _group_by_stage(steps: list[PlanStep]) -> list[tuple[int, list[PlanStep]]]:
    """Return ``[(stage, steps)]`` ordered by ascending stage."""
    by_stage: dict[int, list[PlanStep]] = {}
    for step in steps:
        by_stage.setdefault(step.stage, []).append(step)
    return [(stage, by_stage[stage]) for stage in sorted(by_stage)]


def _build_planner_messages(
    *,
    planner_prompt: str,
    user_text: str,
    tools: list[str],
    max_steps: int,
    completed_outputs: dict[int, str],
    replan_reason: str | None,
    validation_error: str | None,
) -> list[dict[str, Any]]:
    """Build the OpenAI ``messages`` payload for the planner call."""
    contract = (
        "Output a single JSON object: "
        '{"goal": "<string>", "steps": [{"id": <int>, "stage": <int>, '
        '"description": "<string>", "tool": "<tool name|synthesize>", '
        '"args": {...}, "success_criterion": "<string>", "depends_on": [<int>, ...]}]}.'
        f" Constraints: 1 <= len(steps) <= max_steps={max_steps}; ids unique positive ints; "
        "every depends_on entry must reference a step with a strictly lower stage; "
        f"available tools: {', '.join(tools) or '(none)'} plus the literal 'synthesize'."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": (planner_prompt or "") + "\n\n" + contract},
        {"role": "user", "content": user_text},
    ]
    if completed_outputs:
        completed_blob = "\n".join(f"step_{sid}.output: {out}" for sid, out in sorted(completed_outputs.items()))
        messages.append(
            {
                "role": "user",
                "content": "Completed step outputs (do not redo):\n" + completed_blob,
            }
        )
    if replan_reason:
        messages.append(
            {
                "role": "user",
                "content": f"Replan requested by the executor: {replan_reason}",
            }
        )
    if validation_error:
        messages.append(
            {
                "role": "user",
                "content": (
                    "Your previous response was rejected by the validator: "
                    + validation_error
                    + " — fix the JSON and reply with a valid plan only."
                ),
            }
        )
    return messages


def _build_executor_messages(
    *,
    executor_prompt: str,
    user_text: str,
    step: PlanStep,
    resolved_args: dict[str, Any],
    tool_result: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build the OpenAI ``messages`` payload for one PE executor step call."""
    tool_block: str
    if tool_result is None:
        tool_block = "Tool: synthesize (no tool invocation)"
    else:
        tool_block = f"Tool result (is_error={bool(tool_result.get('is_error'))}): {tool_result.get('content', '')!r}"
    structured = (
        f"Step description: {step.description}\n"
        f"Tool: {step.tool}\n"
        f"Args: {json.dumps(resolved_args, sort_keys=True)}\n"
        f"Success criterion: {step.success_criterion}\n"
        f"{tool_block}\n\n"
        "Reply with a single JSON object: "
        '{"step_id": <id>, "status": "success"|"failure"|"replan_requested", '
        '"output": "<text>", "notes": "<text>"}.'
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": executor_prompt or ""},
        {"role": "user", "content": user_text},
        {"role": "user", "content": structured},
    ]
    return messages


def _parse_step_response(content: str) -> dict[str, str]:
    """Best-effort parse of the executor LLM JSON envelope."""
    trimmed = (content or "").strip()
    try:
        data = json.loads(trimmed) if trimmed else {}
    except json.JSONDecodeError:
        return {
            "status": "failure",
            "output": "",
            "notes": "",
            "reason": "executor_response_invalid_json",
        }
    if not isinstance(data, dict):
        return {
            "status": "failure",
            "output": "",
            "notes": "",
            "reason": "executor_response_not_object",
        }
    raw_status = data.get("status")
    if raw_status not in {"success", "failure", "replan_requested"}:
        return {
            "status": "failure",
            "output": str(data.get("output") or ""),
            "notes": str(data.get("notes") or ""),
            "reason": f"executor_response_invalid_status:{raw_status!r}",
        }
    output = data.get("output", "")
    if not isinstance(output, str):
        output = json.dumps(output)
    notes = data.get("notes", "")
    if not isinstance(notes, str):
        notes = json.dumps(notes)
    reason = data.get("reason", "")
    if not isinstance(reason, str):
        reason = ""
    return {
        "status": str(raw_status),
        "output": output,
        "notes": notes,
        "reason": reason,
    }


def _format_step_outputs(step_outputs: dict[int, str]) -> str:
    """Format step outputs as a deterministic scratchpad blob."""
    return "\n".join(f"step_{sid}.output: {out}" for sid, out in sorted(step_outputs.items()))


__all__ = [
    "DEFAULT_MAX_STEPS",
    "MAX_PLANNER_RETRIES",
    "MAX_REPLANS",
    "PlanExecuteExecutor",
]
