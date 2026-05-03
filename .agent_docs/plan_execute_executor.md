# Plan & Execute Executor (US-007)

`mode = "plan_execute"` agents drive the same A2A endpoint built in US-005. The
executor implements a planner pass + stage-parallel executor pass + final
synthesis call, with bounded planner retries and replans.

## Pipeline

1. **Planner call** (`executor.plan_execute.planner` span). The planner LLM is
   asked to emit a strict JSON plan:

   ```json
   {
     "goal": "...",
     "steps": [
       {"id": 1, "stage": 1, "description": "...", "tool": "search",
        "args": {...}, "success_criterion": "...", "depends_on": []}
     ]
   }
   ```

   Validation rules (`services/plan_validator.py`):
   - `1 <= len(steps) <= max_steps`.
   - `tool` is in the agent's MCP tools cache OR equals the literal
     `"synthesize"`.
   - Step `id`s are unique positive integers.
   - Every value in `depends_on` references a step in a strictly LOWER stage
     (no forward / same-stage dependencies).

   The error string is part of the contract — tests assert on:
   - `max_steps={N}` substring whenever the count exceeds the budget.
   - `unknown tool: {name}` substring for unknown tools.
   - `forward dependency` substring for forward dependencies.

   On validation failure the planner is retried ONCE with the validation
   message inlined into the next prompt. A second failure ends the run with
   `stop_reason = "planning_failed"`.

2. **Executor pass** (`executor.plan_execute.step` spans). Steps are grouped by
   stage in ascending order. Within a stage:
   - `services/plan_substitution.substitute_args` resolves
     `${step_N.output}` references using prior stage outputs.
   - The MCP tool is invoked (FR-018 retry via the shared
     `executors.base.invoke_mcp_tool`). The literal tool name `synthesize`
     skips MCP invocation.
   - The executor LLM is asked to evaluate the step against
     `success_criterion`; it must return
     `{"step_id", "status", "output", "notes"}` with status
     `success | failure | replan_requested`.
   - Steps within a stage run concurrently via `asyncio.gather`; steps in
     DIFFERENT stages always run sequentially.

3. **Replan**. A `replan_requested` outcome triggers a fresh planner call
   carrying the completed step outputs and the requested reason. The new plan
   replaces the not-yet-completed steps; success outputs from earlier steps
   persist. Up to 3 replans (= 4 planner calls including the initial one); a
   4th request ends the run with `stop_reason = "replan_exceeded"`.

4. **Failure**. A `failure` outcome aborts the whole run with
   `stop_reason = "step_failed"`.

5. **Token guardrail**. A cumulative `tokens_out >= max_tokens` check after each
   stage skips the remaining stages and forces a single synthesis call (the
   `should_skip_synthesis` heuristic from US-006 still applies). Final
   `stop_reason = "max_tokens"`, `status = "completed"`.

6. **Synthesis** (`executor.plan_execute.synthesis` span). One LLM call with
   no tools turns the accumulated step outputs into the final answer. The run
   row is then finalized with the synthesis text in `final_answer`.

## SSE shape

- `TaskStatusUpdate {state: "working"}` (initial).
- `TaskStatusUpdate {state: "working", payload: {phase: "planning"}}`.
- Per stage: `TaskStatusUpdate {state: "working", payload: {phase: "executing", stage: N, steps: [<id>, ...]}}`.
- `TaskStatusUpdate {state: "working", payload: {phase: "synthesizing"}}`.
- `TaskArtifactUpdate` carrying the synthesis text.
- `TaskStatusUpdate {state: "completed"}`.

## Persistence

- `agent_runs.plan` is stamped with the INITIAL plan dict (deep-equal with the
  planner mock's output).
- `agent_run_steps` rows for a successful run:
  - 1 row `role = "plan"` per planner call (initial + each replan).
  - 1 row `role = "step_result"` per executed step, with `stage`, `step_id`,
    and `step_status` populated.
  - 1 row `role = "synthesis"` for the final answer.
- `loops` is reserved for the synthesis defensive cap (0 or 1; stays at 0 on
  early failures).

## Defaults

- `max_steps`: agent's configured value (1..50, form default 20).
- `max_tokens`: agent's configured value (form default 16000 for PE).
- `max_loops` is irrelevant for PE; the form sets it to 1 by default.
