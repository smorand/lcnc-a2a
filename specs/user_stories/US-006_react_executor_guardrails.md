# US-006: ReAct mode executor with guardrails and embeddings

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 6
> Depends On: US-005
> Complexity: L

## Objective

Implement the ReAct executor (Thought → Act → Observe loop) with the embedding-similarity stop condition, the `max_loops` / `max_tokens` guardrails (with forced synthesis when a guardrail is hit), the embedding retry policy, and the loop's specific OTel span shape. After this story, `mode = "react"` agents drive the same A2A endpoint built in US-005 to completion.

## Technical Context

### Stack

- All from US-005.
- Embedding HTTP calls go to the agent's configured provider (OpenRouter or openai_compatible) at `/v1/embeddings`.
- Default embedding model: `text-embedding-3-small` (OpenRouter id `openai/text-embedding-3-small`); for `openai_compatible`, the agent's `embedding_model` column (created in US-002) is used.

### Relevant File Structure

```
src/lcnc_a2a/
├── executors/
│   ├── react.py                  # ReActExecutor
│   └── synthesis.py              # shared force-synthesis helper (used by ReAct now, by PE in US-007)
├── llm/
│   └── embeddings.py             # embed() with retry policy
├── services/
│   └── similarity.py             # cosine_similarity()
└── tests/e2e/
    ├── test_a2a_react.py
    ├── test_react_guardrails.py
    ├── test_react_embeddings.py
    └── test_react_otel.py
```

### Existing Patterns

- The executor dispatcher from US-005 selects `ReActExecutor` when `agent.mode == "react"`. Until this story, the dispatcher raises `NotImplementedError` for ReAct.
- Tool retry (FR-018), LLM error handling (FR-014 carry-over), cancellation, OTel `executor.<mode>.iter` spans, and config snapshot all reuse the helpers from US-005.

### ReAct loop shape

Each iteration:

1. Send prompt: `system_prompt` + the scratchpad (ordered prior thoughts/actions/observations) + the user message + tool catalog (OpenAI tools format).
2. Parse the LLM output into either `(thought, tool_call)` (continue) or `(final_answer)` (stop). Unparseable output → record an `error` step with content `parse_error`, count the iteration toward `max_loops`, continue.
3. If a tool call: invoke the MCP tool (with FR-018 retry), append the observation to the scratchpad.
4. After step 2, compute the cosine similarity between the embedding of the current iteration's textual output and the previous iteration's. If `similarity >= similarity_threshold`, stop with `stop_reason = "similarity"` and return the **previous iteration's candidate** as the final answer. (First iteration has no previous; similarity check applies from iteration 2 onwards.)

### Guardrails (FR-017)

- After each iteration, check cumulative loop count and cumulative LLM tokens.
- On `loops >= max_loops` OR `tokens >= max_tokens` (and no final answer yet), perform ONE final LLM call asking it to synthesize an answer from the accumulated scratchpad.
- The synthesis call's tokens count toward the total; if the synthesis call would exceed `max_tokens` by > 50%, skip it and end `failed` with `stop_reason = "guardrail_exceeded_no_synthesis"`.
- Synthesis success → `status = "completed"`, `stop_reason ∈ {"max_loops", "max_tokens"}` (whichever fired first).

### Embedding retry (FR-019)

- 3 total attempts (1 + 2 retries) with backoff `200ms, 600ms, 1800ms`.
- Retry only on transport / 5xx / 429. 4xx other than 429 fail immediately.
- After 3 failures, the run ends with `status = "failed"`, `stop_reason = "embedding_unavailable"`.

### SSE shape

Per iteration: a `TaskStatusUpdate {state: "working", payload: {loop: N, phase: "thought" | "action" | "observation"}}`. Final: `TaskArtifactUpdate` carrying the answer, then `TaskStatusUpdate {state: "completed"}`.

## Functional Requirements

### FR-015: ReAct mode executor

See "ReAct loop shape" above. Threshold comparison is `>=` (inclusive). `loops` field on `agent_runs` equals the number of iterations actually executed (no off-by-one).

### FR-017: Guardrails (max_loops, max_tokens, force synthesis on hit)

See "Guardrails" above.

### FR-019: Embedding retry policy

See "Embedding retry" above.

## Acceptance Tests

> Acceptance tests are mandatory: 100% must pass via `make test`. Loop until green.

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `respx`-mocked embeddings | Configurable mock returning vectors with controlled cosine similarity. | auto-generated | ready |
| Helper `make_embedding(seed)` | Deterministic 1536-dim vector generator for similarity tests (returns a normalized vector keyed off `seed`). | auto-generated | ready |
| ReAct prompt fixture | Short ReAct system prompt that instructs the model to emit `Thought:` / `Action:` lines (the executor's parser keys off this format). | auto-generated | ready |

### Happy Path Tests

#### E2E-060: ReAct happy path stops by final answer

- **Category:** happy
- **Scenario:** SC-010
- **Requirements:** FR-015
- **Preconditions:**
  - Started ReAct agent `A` with `max_loops = 10`, `similarity_threshold = 0.95`, no MCP servers needed for this test if final answer comes after one tool call (or with a single iteration).
  - LLM mock iter 1: emits a thought + tool call (any tool with a no-op MCP fixture). Iter 2 (after tool observation): final answer `"final"` (no tool call).
  - Embedding mock returns dissimilar vectors (cosine < 0.95).
- **Steps:**
  - When the A2A client sends a message.
  - Then the SSE stream emits, in order, `{phase: "thought"}`, `{phase: "action"}`, `{phase: "observation"}`, then a final `TaskArtifactUpdate` with text `"final"`, then `TaskStatusUpdate {state: "completed"}`.
  - And `agent_runs.loops = 2`, `stop_reason = "final"`, `status = "completed"`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-061: ReAct stops by similarity at iter 3

- **Category:** happy
- **Scenario:** SC-010
- **Requirements:** FR-015
- **Preconditions:**
  - ReAct agent with `max_loops = 10`, `similarity_threshold = 0.95`.
  - LLM mock: iter 1, 2, 3 all emit a thought + tool call (never finalizes).
  - Embedding mock: returns the SAME vector for iter 2 and iter 3 outputs (cosine = 1.0); iter 1's vector is different from iter 2's.
- **Steps:**
  - When the client sends a message.
  - Then `agent_runs.loops = 3`, `stop_reason = "similarity"`, `status = "completed"`.
  - And the trace contains at least one `agent_run_steps` row with `similarity_to_prev >= 0.95`.
  - And `final_answer` equals exactly the candidate text emitted by iter 2 (the previous iteration's output, per the spec).
- **Cleanup:** Truncate.
- **Priority:** Critical

### Edge Case and Error Tests

#### E2E-062: ReAct hits `max_loops` → forces synthesis

- **Category:** failure
- **Scenario:** SC-010
- **Requirements:** FR-017
- **Preconditions:**
  - ReAct agent with `max_loops = 3`, `max_tokens = 8000`.
  - LLM mock always emits a thought + tool call (never finalizes). Embedding mock returns dissimilar vectors.
  - Synthesis LLM mock returns assistant content `"synthesized answer"` with low token usage.
- **Steps:**
  - When the client sends a message.
  - Then `agent_runs.loops = 3`, `stop_reason = "max_loops"`, `final_answer = "synthesized answer"`, `status = "completed"`.
  - And the LLM mock recorded exactly 4 calls (3 iterations + 1 synthesis).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-063: ReAct hits `max_tokens` → forces synthesis

- **Category:** failure
- **Scenario:** SC-010
- **Requirements:** FR-017
- **Preconditions:**
  - ReAct agent with `max_loops = 50`, `max_tokens = 200`.
  - LLM mock returns `usage: {prompt_tokens: 0, completion_tokens: 100}` per iteration.
- **Steps:**
  - When the run executes.
  - Then after iter 2 cumulative output tokens reach 200 and the executor performs ONE synthesis call (which the test mock returns within budget).
  - And `agent_runs.stop_reason = "max_tokens"`, `status = "completed"`, `final_answer` equals the synthesis mock's content.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-064: Synthesis would exceed `max_tokens` by > 50% → skip synthesis, fail

- **Category:** failure
- **Scenario:** SC-010
- **Requirements:** FR-017
- **Preconditions:**
  - ReAct agent with `max_tokens = 200`. The cumulative tokens after iter N reach 199. The executor's heuristic estimates the synthesis call would consume > 100 additional tokens (test forces this estimate via a fixture or by configuring the synthesis mock to declare a high-cost prompt size).
- **Steps:**
  - When the run hits the guardrail.
  - Then `agent_runs.status = "failed"`, `stop_reason = "guardrail_exceeded_no_synthesis"`.
  - And the LLM mock recorded zero synthesis calls (the synthesis route was never hit).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-065: Embedding fails 3× → run hard-fails

- **Category:** failure
- **Scenario:** SC-010
- **Requirements:** FR-019
- **Preconditions:**
  - ReAct agent. LLM mock makes ≥ 2 iterations (so similarity is invoked). Embedding mock returns HTTP 503 every time.
- **Steps:**
  - When the client sends a message.
  - Then the embedding mock recorded exactly 3 calls (1 + 2 retries) at iteration 2's similarity check.
  - And `agent_runs.status = "failed"`, `stop_reason = "embedding_unavailable"`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-066: Tool fails 3× in ReAct → tool_result error to LLM, loop continues

- **Category:** failure
- **Scenario:** SC-010
- **Requirements:** FR-018
- **Preconditions:**
  - ReAct agent with MCP tool `flaky` always returning errors. LLM mock iter 1 calls `flaky`; iter 2 (after error observation) emits a final answer.
- **Steps:**
  - When the run executes.
  - Then the MCP fixture recorded exactly 3 calls to `flaky` per tool invocation.
  - And the trace contains an observation step with content reflecting the error.
  - And `agent_runs.status = "completed"`, `loops = 2`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-067: ReAct unparseable LLM output counts as a consumed loop

- **Category:** failure
- **Scenario:** SC-010
- **Requirements:** FR-015
- **Preconditions:**
  - ReAct agent with `max_loops = 2`. LLM iter 1 returns non-parseable text (no `Thought:` / `Action:` structure). Iter 2 returns a final answer.
- **Steps:**
  - When the run executes.
  - Then `agent_runs.loops = 2`, `status = "completed"`.
  - And iter 1's row in `agent_run_steps` has `role = "error"` and `content` containing the substring `parse_error`.
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-068: ReAct with `similarity_threshold = 0.99` does NOT stop at 0.97

- **Category:** edge
- **Scenario:** SC-010
- **Requirements:** FR-015
- **Preconditions:**
  - ReAct agent with `similarity_threshold = 0.99`, `max_loops = 5`. Embedding mock returns vectors with cosine 0.97 between iter 2 and iter 3 outputs. LLM mock never finalizes.
- **Steps:**
  - When the run executes.
  - Then `agent_runs.stop_reason = "max_loops"` (not `"similarity"`).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-069: ReAct with similarity exactly 0.95 (threshold inclusive)

- **Category:** edge
- **Scenario:** SC-010
- **Requirements:** FR-015
- **Preconditions:**
  - ReAct agent with `similarity_threshold = 0.95`. Embedding mock returns vectors with cosine exactly 0.95 between iter 2 and iter 3 outputs.
- **Steps:**
  - When the run executes.
  - Then `agent_runs.stop_reason = "similarity"` (the comparison is `>=`).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-070: Per-loop trace persisted

- **Category:** side effect
- **Scenario:** SC-010
- **Requirements:** FR-009
- **Preconditions:**
  - A 3-iteration ReAct run that completes (e.g., similar to E2E-060 extended).
- **Steps:**
  - When the run completes.
  - Then `agent_run_steps` for that run contains rows with roles in order including `thought`, `action`, `observation` for each iteration.
  - And from iter 2 onwards, every `agent_run_steps` row corresponding to an iteration's representative entry has non-null `tokens_in`, `tokens_out`, AND `similarity_to_prev`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-071: ReAct trace JSONL contains no prompt / response / embedding-input text

- **Category:** side effect / security
- **Scenario:** SC-010
- **Requirements:** FR-024
- **Preconditions:**
  - OTel JSONL exporter is wired to a `tmp_path` file.
  - User message contains the unique substring `unique-secret-PROMPT-react-token`. LLM mock assistant content contains `unique-secret-RESPONSE-react-token`. Embedding input strings include `unique-secret-EMBED-react-input`.
- **Steps:**
  - When a ReAct run completes.
  - Then the trace file is non-empty and contains at least one span whose `name` starts with `llm.embed`.
  - And `grep -c` for each of the three unique substrings returns 0.
  - And the `llm.embed` span attributes include exactly the keys `model`, `provider`, `tokens.prompt`, `tokens.completion`, `cost.usd`, `duration.ms`, `request_id` (no others).
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-072: ReAct keeps loop count == iterations executed (no off-by-one)

- **Category:** state transition
- **Scenario:** SC-010
- **Requirements:** FR-015
- **Preconditions:**
  - A ReAct run that runs exactly 5 iterations and finalizes on iteration 5.
- **Steps:**
  - When the run completes.
  - Then `agent_runs.loops = 5` (not 4 nor 6).
- **Cleanup:** Truncate.
- **Priority:** High

## Constraints

### Files Not to Touch

- US-005's executor base, dispatcher, cancellation, OTel exporter — extend via composition.
- The A2A endpoint routes — the dispatcher handles mode selection.

### Dependencies Not to Add

- No new runtime dependencies (use `numpy` ONLY if cosine similarity demands it; a pure-Python implementation over float lists is also acceptable and preferred to avoid a heavy dependency).

### Patterns to Avoid

- Do NOT compute cosine on un-normalized vectors via the dot-product alone; either normalize or divide by both norms.
- Do NOT skip the iter-2 similarity check when iter 1's textual output is empty; treat empty output as a vector of zeros and downstream similarity as 0 (no stop).
- Do NOT count the synthesis call as an iteration in `agent_runs.loops`.
- Do NOT include embedding input strings in OTel attributes.

### Scope Boundary

- Plan & Execute mode (FR-016, FR-020) is NOT in this story.
- Runs UI (FR-009 page) is NOT in this story.
- Replan / multi-stage parallelism is PE-only and not in scope.

## Non Regression

### Existing Tests That Must Pass

- All US-001..US-005 tests, in particular:
  - The Simple-mode happy paths (US-005) MUST continue to work.
  - The trace redaction test for Simple mode (E2E-057) must still pass.
  - Cross-cutting tests (E2E-090..093) must still pass.

### Behaviors That Must Not Change

- Simple mode behavior (US-005).
- A2A transport layer / Agent Card / auth (US-005).
- Builder UI (US-001..US-004).

### API Contracts to Preserve

- All routes from US-001..US-005.
