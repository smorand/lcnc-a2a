# US-007: Plan & Execute mode executor

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 7
> Depends On: US-005
> Complexity: L

## Objective

Implement the Plan & Execute (PE) mode executor: a planner pass producing a strict-JSON plan, a stage-parallel executor pass with `${step_N.output}` substitution and replan support, a final synthesis call, and validation/retry rules with bounded replans. After this story, `mode = "plan_execute"` agents drive the same A2A endpoint built in US-005 to completion.

## Technical Context

### Stack

- All from US-005.
- `asyncio.gather` for stage-parallel step execution.
- `jsonschema` (or hand-rolled validator) for plan validation.

### Relevant File Structure

```
src/lcnc_a2a/
├── executors/
│   └── plan_execute.py            # PlanExecuteExecutor (planner + executor + replan + synthesis)
├── services/
│   ├── plan_validator.py          # validates the plan JSON shape and dependency rules
│   └── plan_substitution.py       # ${step_N.output} resolution
└── tests/e2e/
    ├── test_a2a_plan_execute.py
    ├── test_pe_planner.py
    ├── test_pe_replan.py
    └── test_pe_substitution.py
```

### Existing Patterns

- The dispatcher from US-005 selects `PlanExecuteExecutor` when `agent.mode == "plan_execute"`.
- Tool retry (FR-018), force synthesis on guardrail (FR-017), cancellation, OTel spans (FR-024) reuse helpers.
- Per-context memory (FR-021) and token tracking (FR-022) reuse helpers.

### Plan JSON shape (strict)

```json
{
  "goal": "string",
  "steps": [
    {
      "id": 1,
      "stage": 1,
      "description": "string",
      "tool": "search" | "synthesize" | "<one of the catalog tool names>",
      "args": {"query": "..."},
      "success_criterion": "string",
      "depends_on": [<int>, ...]
    }
  ]
}
```

### Plan validation rules (FR-016, FR-020)

- `1 <= len(steps) <= max_steps`.
- Every `tool` must be present in the agent's MCP tools_cache OR equal the literal `"synthesize"`.
- Every `id` is a unique positive integer.
- Every value in `depends_on` references a step whose `stage` is strictly LOWER than the dependent step's `stage` (no forward / same-stage dependencies).
- Validation failure causes ONE retry of the planner with the validation error inlined into the planner prompt; second failure ends the run with `stop_reason = "planning_failed"`.

### Executor pass

- Group steps by `stage` (ascending).
- For each stage:
  - For each step in the stage, render the `executor_prompt` with `step.description`, `step.tool`, resolved `args` (with `${step_N.output}` substituted from prior stage outputs), `step.success_criterion`.
  - Run all member steps concurrently via `asyncio.gather`.
  - Each step calls the LLM with the executor prompt; the LLM emits a JSON `{step_id, status, output, notes}`.
  - On `status = "failure"` for any step, abort the whole run with `stop_reason = "step_failed"`.
  - On `status = "replan_requested"` for any step, invoke the planner again with the partial outputs and the requested reason. Up to 3 replans per task; 4th fails with `stop_reason = "replan_exceeded"`. After replan, the `id` and `stage` numbering of the newly-returned steps follow the rules: replan replaces remaining (not-yet-completed) steps; completed step results are preserved.

### Synthesis (final)

- After all steps complete, ONE final LLM call asks the model to synthesize the answer from the step outputs (the prompt template is part of the executor implementation; not user-configurable in this MVP).
- The synthesis call's tokens count toward `max_tokens`.
- `agent_runs.plan` is persisted as the planner's output JSON (deep-equal preserved).

### SSE shape

- Planner start: `TaskStatusUpdate {state: "working", payload: {phase: "planning"}}`.
- Per stage: `TaskStatusUpdate {state: "working", payload: {phase: "executing", stage: N, steps: [<id>, ...]}}`.
- Synthesis: `TaskStatusUpdate {state: "working", payload: {phase: "synthesizing"}}`.
- Final: `TaskArtifactUpdate` with the synthesis text, then `TaskStatusUpdate {state: "completed"}`.

### Guardrails for PE (FR-017)

- `max_loops` here applies to synthesis (defensive cap = 1).
- `max_tokens` is a hard ceiling on total tokens across planner + executor + synthesis (default 16000).
- On `max_tokens` hit, force a single synthesis call from accumulated step outputs (same skip-if-too-expensive rule from US-006 applies).

## Functional Requirements

### FR-016: Plan & Execute mode executor

See "Plan JSON shape", "Executor pass", "Synthesis" above.

### FR-020: Plan validation and bounded retry

See "Plan validation rules" above. `max_steps` is the agent's configured value (1..50, default 20).

## Acceptance Tests

> Acceptance tests are mandatory: 100% must pass via `make test`. Loop until green.

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| PE prompt fixtures | Short `planner_prompt` and `executor_prompt` strings used for all PE tests. | auto-generated | ready |
| Three-tool MCP fixture | An MCP fixture exposing tools `search`, `get_market_data`, `compute_ratios` (each a no-op returning a stub success envelope). | auto-generated | ready |
| Sleep-tool MCP fixture | An MCP fixture exposing a tool `slow` that sleeps 200 ms before responding. Used in E2E-074 for parallelism timing. | auto-generated | ready |
| `plan_json(steps)` builder | Test helper that constructs the JSON envelope returned by the planner mock. | auto-generated | ready |
| `respx`-mocked planner / executor / synthesis | Configurable LLM mock that distinguishes planner vs executor vs synthesis calls (e.g., by prompt prefix or by sequencing). | auto-generated | ready |

### Happy Path Tests

#### E2E-073: PE happy path with 3 sequential steps

- **Category:** happy
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Started PE agent `A` with `planner_prompt`, `executor_prompt` set; MCP tool catalog `[search, get_market_data, compute_ratios]`.
  - Planner mock returns a valid plan: 3 steps in stages 1, 2, 3, each calling one of the three tools, each with a `success_criterion`.
  - Executor LLM mock returns `{status: "success", output: "..."}` for each step.
  - Synthesis mock returns `"final"`.
- **Steps:**
  - When the client sends a message.
  - Then the SSE stream emits `{phase: "planning"}`, then for each stage in order `{phase: "executing", stage: N, steps: [<id>]}`, then `{phase: "synthesizing"}`, then a final artifact `"final"`, then `state: "completed"`.
  - And the MCP fixtures recorded one call to each tool, and the calls happened in stage order.
  - And `agent_runs.plan` JSON deep-equals the planner mock's output.
  - And `agent_run_steps` for the run contains, in order: 1 row with `role = "plan"`, then 3 rows with `role = "step_result"` (one per step), then 1 row with `role = "synthesis"`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-074: PE parallel stage executes steps concurrently

- **Category:** happy
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Started PE agent with the `slow` MCP tool (sleeps 200 ms per call). Planner returns 3 steps ALL in `stage = 1`, each invoking `slow` via the executor.
  - Executor LLM mock returns `{status: "success", output: "ok"}` immediately after the tool call.
- **Steps:**
  - When the run executes.
  - Then the wall-clock between the SSE `{phase: "executing", stage: 1, ...}` event and the `{phase: "synthesizing"}` event is strictly less than 500 ms (would be ≥ 600 ms if executed sequentially).
  - And all 3 `slow` tool calls were recorded.
- **Cleanup:** Truncate.
- **Priority:** Critical

### Edge Case and Error Tests

#### E2E-075: Planner returns invalid JSON twice → `planning_failed`

- **Category:** failure
- **Scenario:** SC-011
- **Requirements:** FR-020
- **Preconditions:**
  - Planner mock returns the literal text `not json` on both calls.
- **Steps:**
  - When the run executes.
  - Then the planner mock recorded exactly 2 calls.
  - And `agent_runs.status = "failed"`, `stop_reason = "planning_failed"`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-076: Planner returns > max_steps → retry once with error, then fail

- **Category:** failure
- **Scenario:** SC-011
- **Requirements:** FR-020
- **Preconditions:**
  - PE agent with `max_steps = 3`. Planner mock returns 5 steps on both calls.
- **Steps:**
  - When the run executes.
  - Then the planner mock's second-call recorded prompt CONTAINS the literal substring `max_steps=3` (proves the validation error was inlined into the retry prompt).
  - And `agent_runs.status = "failed"`, `stop_reason = "planning_failed"`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-077: Plan referencing unknown tool is rejected, retry once

- **Category:** failure
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Planner mock first returns a plan calling tool `nonexistent`; second returns a valid plan calling tool `search`.
- **Steps:**
  - When the run executes.
  - Then the run completes (`status = "completed"`).
  - And the planner mock recorded exactly 2 calls.
  - And the second planner prompt contains the literal substring `unknown tool: nonexistent`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-078: Plan with forward dependency is rejected, retry once

- **Category:** failure
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Planner mock first returns a plan where step 1 (stage 1) has `depends_on: [2]` and step 2 is in stage 2; second returns a valid plan.
- **Steps:**
  - When the run executes.
  - Then the planner mock's second-call recorded prompt contains the literal substring `forward dependency`.
  - And the run completes successfully.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-079: PE total tokens hit `max_tokens` → force synthesis

- **Category:** failure
- **Scenario:** SC-011
- **Requirements:** FR-017
- **Preconditions:**
  - PE agent with `max_tokens = 300`. Each LLM call (planner, each step executor, synthesis) returns `usage: {prompt_tokens: 0, completion_tokens: 100}`.
- **Steps:**
  - When the run executes a plan of 5 steps.
  - Then after step 2 cumulative tokens reach 300; remaining steps are SKIPPED; ONE synthesis call is performed (the synthesis mock returns within budget).
  - And `agent_runs.stop_reason = "max_tokens"`, `status = "completed"`.
  - And only 2 of the 5 step's MCP tool calls were recorded.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-080: Replan exceeded after 3 replans → `replan_exceeded`

- **Category:** failure
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Each executor step returns `{status: "replan_requested", reason: "need_more_info"}`. Each replan returns a new (valid) plan that the executor will again replan-request.
- **Steps:**
  - When the run executes.
  - Then the planner mock recorded exactly 4 calls (initial + 3 replans).
  - And `agent_runs.status = "failed"`, `stop_reason = "replan_exceeded"`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-081: PE plan with 1 step in 1 stage works

- **Category:** edge
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Planner mock returns a plan with a single step in stage 1 calling the `synthesize` tool (literal); executor LLM mock for that step returns `{status: "success", output: "x"}`.
- **Steps:**
  - When the run executes.
  - Then `agent_runs.status = "completed"`.
  - And `agent_run_steps` for the run has 1 `plan` row + 1 `step_result` row + 1 `synthesis` row.
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-082: PE plan with `${step_N.output}` substitution

- **Category:** edge
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Planner returns a plan where step 1 (stage 1) calls a tool that returns output `"42"`. Step 2 (stage 2) has args `{value: "${step_1.output}"}`.
- **Steps:**
  - When the run executes.
  - Then the MCP fixture for step 2's tool was called with args `{value: "42"}` (substitution applied before the tool call).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-083: PE persists plan and per-step results

- **Category:** side effect
- **Scenario:** SC-011
- **Requirements:** FR-009, FR-016
- **Preconditions:**
  - A 3-step PE run that completes (e.g., as in E2E-073).
- **Steps:**
  - When the run completes.
  - Then `agent_runs.plan` JSON deep-equals the planner mock's output.
  - And `agent_run_steps` for the run contains exactly 3 rows with `role = "step_result"`, each with non-null `step_id`, `stage`, `step_status` populated.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-084: PE replan replaces remaining steps, keeps completed ones

- **Category:** side effect
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - Planner first returns a plan with 4 steps in stages 1, 2, 3, 4. Executor LLM mock: step 1 succeeds; step 2 returns `replan_requested`. Replan planner returns a new plan with 2 new steps in stages 3, 4 (new ids).
- **Steps:**
  - When the run completes.
  - Then `agent_run_steps` for the run includes a `step_result` for the original step 1 (status `success`), one for step 2 (status `replan_requested`), and exactly 2 new `step_result` rows for the replan's 2 new steps.
  - And the original steps 3 and 4 from the FIRST plan were NOT executed (the MCP fixture recorded zero calls for those tools / step ids).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-085: PE state transitions running → completed after synthesis

- **Category:** state transition
- **Scenario:** SC-011
- **Requirements:** FR-016
- **Preconditions:**
  - A PE run mid-execution (synthesis mock stalled). `agent_runs.status` is `running` while stalled.
- **Steps:**
  - When the synthesis mock unblocks.
  - Then `agent_runs.status = "completed"` and `completed_at` is non-null.
- **Cleanup:** Truncate.
- **Priority:** High

## Constraints

### Files Not to Touch

- US-005's executor base / dispatcher / OTel exporter — extend via composition.
- US-006's ReAct executor — independent code path; do not share state.

### Dependencies Not to Add

- Allowed: `jsonschema` (optional, for plan validation). Pure-Python validation is acceptable too; choose one.
- Disallowed: any LLM-orchestration framework (LangChain, LlamaIndex, etc.).

### Patterns to Avoid

- Do NOT execute steps from different stages in parallel; only steps within the SAME stage.
- Do NOT propagate raw planner JSON to the LLM via the executor prompt; render only the resolved `description`, `tool`, `args`, and `success_criterion` per step.
- Do NOT count the synthesis call as a step in `agent_runs.loops` (PE's `loops` field is reserved for synthesis defensive cap = 1).
- Do NOT lose completed step outputs across a replan; persist them and pass them to the planner's replan call.

### Scope Boundary

- Runs UI (FR-009 page) is NOT in this story.
- Tool-name collision handling across MCP servers (TBD-002) is NOT required.

## Non Regression

### Existing Tests That Must Pass

- All US-001..US-006 tests, in particular:
  - Simple-mode (US-005) and ReAct-mode (US-006) executors must continue to function unchanged.
  - Trace redaction tests (E2E-057, E2E-071) still pass.
  - Cross-cutting tests (E2E-090..093) still pass.

### Behaviors That Must Not Change

- Simple and ReAct executors are not modified.
- A2A transport, Agent Card, auth, 503 semantics unchanged.
- Builder UI (US-001..US-004) unchanged.

### API Contracts to Preserve

- All routes from US-001..US-005.
