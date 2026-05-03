# US-005: A2A endpoint, Agent Card, authentication, Simple mode executor (full)

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 5
> Depends On: US-003, US-004
> Complexity: XL (intentionally thicker than the 5-15 test guideline; merges what would have been three smaller stories: A2A transport, Simple no-tools, and Simple with-tools+cancellation+cross-cutting)

## Objective

Stand up the public A2A protocol surface for every agent: per-agent POST `/agents/<id>` accepting `SendStreamingMessage` with SSE streaming, the Agent Card endpoint at `/agents/<id>/.well-known/agent-card.json`, Bearer API-key authentication with constant-time comparison, and the `503 agent_stopped` semantics. Then implement the **Simple** mode executor end to end: per-context conversation memory, MCP tool calls with retry, OpenRouter cost tracking, OpenTelemetry JSONL spans (with the strict redaction rules), in-flight run cancellation on delete, and concurrent-context isolation. After this story an external A2A client can drive a Simple agent end-to-end; the ReAct and PE executors plug into the same dispatcher in subsequent stories.

## Technical Context

### Stack

- All from US-002–US-004 plus:
  - The official **A2A protocol** types (envelope shapes for `SendStreamingMessage`, `TaskStatusUpdate`, `TaskArtifactUpdate`, Agent Card schema). The protocol is defined externally; this story implements the server side per the spec.
  - `httpx.AsyncClient` for OpenRouter / OpenAI-compatible LLM calls.
  - `respx` for mocking the LLM HTTP boundary in tests.
  - `opentelemetry-sdk` (already brought in by US-001) plus the JSONL exporter scaffolded in US-001.

### Relevant File Structure

```
src/lcnc_a2a/
├── a2a/
│   ├── __init__.py
│   ├── envelope.py                  # SendStreamingMessage, TaskStatusUpdate, TaskArtifactUpdate types
│   ├── card.py                      # AgentCard builder
│   └── sse.py                       # SSE streaming helpers
├── routes/
│   └── a2a.py                       # POST /agents/<id> + GET /agents/<id>/.well-known/agent-card.json
├── auth/
│   └── api_key.py                   # constant-time bearer validation
├── llm/
│   ├── __init__.py
│   ├── provider.py                  # LlmProvider ABC + OpenRouterProvider, OpenAiCompatibleProvider
│   └── tool_format.py               # MCP tools_cache → OpenAI tools[] format
├── executors/
│   ├── __init__.py
│   ├── base.py                      # ExecutorBase (cancel token, snapshot, OTel)
│   ├── dispatcher.py                # mode → executor; raises NotImplementedError for ReAct/PE in US-005
│   └── simple.py                    # SimpleExecutor
├── services/
│   ├── runs.py                      # AgentRun lifecycle (create, append step, finalize)
│   ├── messages.py                  # context get_or_create + append + soft/hard caps
│   └── cancellation.py              # CancellationRegistry (run_id -> asyncio.Event)
└── tests/e2e/
    ├── test_a2a_auth.py
    ├── test_a2a_card.py
    ├── test_a2a_simple.py
    ├── test_a2a_simple_tools.py
    ├── test_a2a_cancellation.py
    ├── test_a2a_concurrency.py
    └── test_a2a_otel.py

alembic/versions/
└── 0004_runs_steps_contexts_messages.py
   # creates / extends agent_runs, agent_run_steps, agent_contexts, agent_messages
   # to their FULL spec schema (config_snapshot, plan, final_answer, stop_reason, etc.)
```

### Existing Patterns

- The minimal `agent_runs` table created in US-002 is **extended** here to its full schema. The cascade-target tables (`agent_run_steps`, `agent_contexts`, `agent_messages`) created empty in US-003 gain real data.
- `MCP tools_cache` from US-004 is read by the executor and translated to the OpenAI tools array.
- Agent ownership / 404-on-mismatch is enforced via the existing service layer for the *builder* surface (UI). The A2A surface uses the per-agent API key for authorization (FR-012).

### Data Model (excerpt)

#### agent_runs (full)

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| agent_id | UUID FK → agents.id ON DELETE CASCADE | |
| context_id | UUID FK → agent_contexts.id | nullable |
| a2a_task_id | VARCHAR(100) | |
| status | VARCHAR(20) | `running`/`completed`/`failed`/`cancelled` |
| stop_reason | VARCHAR(60) | nullable |
| loops | INTEGER | default 0 |
| tokens_in | INTEGER | default 0 |
| tokens_out | INTEGER | default 0 |
| cost_usd | NUMERIC(12,6) | nullable |
| duration_ms | INTEGER | nullable |
| started_at, completed_at | TIMESTAMPTZ | |
| plan | JSONB | nullable, PE only |
| final_answer | TEXT | nullable |
| config_snapshot | JSONB | snapshot of agent config at run start |

#### agent_run_steps

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| run_id | UUID FK → agent_runs.id ON DELETE CASCADE | |
| seq | INTEGER | |
| role | VARCHAR(20) | `thought` / `action` / `observation` / `plan` / `step_result` / `synthesis` / `error` / `assistant` / `tool` |
| content | TEXT | |
| tool_name | VARCHAR(200) | nullable |
| tool_args_json | JSONB | nullable, ≤ 64 KB |
| tool_result_json | JSONB | nullable, ≤ 64 KB |
| tokens_in | INTEGER | nullable |
| tokens_out | INTEGER | nullable |
| similarity_to_prev | DOUBLE PRECISION | nullable |
| stage, step_id | INTEGER | nullable, PE only |
| step_status | VARCHAR(20) | nullable, PE only |
| duration_ms | INTEGER | |
| occurred_at | TIMESTAMPTZ | |
| truncated | BOOLEAN | default false |
| truncated_payload_sha256 | CHAR(64) | nullable |

#### agent_contexts / agent_messages

Per spec §8.

### Auth (FR-012)

- `Authorization: Bearer <key>` parsed; the lookup is `WHERE key_hash = sha256(key) AND agent_id = <path_id> AND revoked_at IS NULL`.
- The comparison MUST use `hmac.compare_digest` (constant time).
- Missing header → 401 with body `{"error": "auth_required"}` (JSON content type).
- Header present but no match → 403 with body `{"error": "auth_invalid"}`.
- Auth is checked BEFORE the started/stopped check on POST. Anonymous request to a stopped agent → 401, NOT 503 (do not leak status).

### 503 (FR-013)

- Authenticated POST `/agents/<id>` to a stopped agent → 503 with body `{"error": "agent_stopped"}`.
- GET `/agents/<id>/.well-known/agent-card.json` to a stopped agent → 503 with body `{"error": "agent_stopped"}`.

### LLM provider abstraction

- `LlmProvider` ABC with `chat(messages, tools, model_id, ...)` and `embed(input)`. OpenRouter and OpenAI-compatible implementations. Cost is read from `usage.cost` for OpenRouter, NULL for openai_compatible.

### OpenTelemetry redaction (FR-024)

- Span attributes for `llm.chat` MUST include only: `model`, `provider`, `tokens.prompt`, `tokens.completion`, `cost.usd`, `duration.ms`, `request_id`. The exporter MUST run a strict allow-list filter at the boundary; any extra attribute is dropped.
- Spans MUST NEVER contain prompt text, response text, user message text, tool I/O, header values, or API keys.

### Per-context memory caps (FR-021)

- Soft cap: 50 messages per context for the LLM payload. When over the cap, drop oldest non-system messages from the LLM payload only; the DB retains all messages.
- Hard cap: 1000 messages per context. Further appends fail with `context_full`.

### Defensive Simple cap (FR-014)

- Simple mode loops only on tool calls. The hard cap is 50 tool iterations. On hit, the run terminates `failed` with `stop_reason = guardrail_exceeded`.

### Tool retry (FR-018)

- Each tool call attempt failure (timeout, non-zero exit, transport error, malformed envelope) retries up to 3 total attempts (1 + 2 retries) with backoff `200ms, 600ms, 1800ms`.
- After the final retry fails, the failure is surfaced to the LLM as a `tool_result` with `is_error=true` and a structured error message; the run continues.

### Cancellation

- A `CancellationRegistry` maps `run_id → asyncio.Event`. On agent delete (US-003 path), the route handler signals the registry; the executor checks the event between iterations and at every awaitable boundary, emits a final `TaskStatusUpdate(state=cancelled)` over SSE, and the cascade DELETE removes the `agent_runs` row (so the executor MUST NOT attempt a final UPDATE on a deleted row).

## Functional Requirements

### FR-010: A2A endpoint per agent

- **Description:** `POST /agents/<id>` accepts an A2A `SendStreamingMessage` (content-type `application/json`) and responds with `text/event-stream`.
- **Inputs:** A2A request envelope (`message` with TextPart, optional FilePart, optional `contextId`, optional `taskId`).
- **Outputs:** SSE stream of `TaskStatusUpdate` and `TaskArtifactUpdate` events per the A2A protocol.
- **Business Rules:**
  - Auth FIRST (FR-012), then status (FR-013), then dispatcher.
  - Missing `contextId` → create a new `agent_contexts` row.
  - Existing `contextId` → load prior persisted messages and reuse them.
  - First SSE event (`TaskStatusUpdate {state: working}`) MUST be emitted within 200 ms of the LLM connection being established.

### FR-011: Agent Card endpoint

- **Description:** `GET /agents/<id>/.well-known/agent-card.json` returns the Agent Card JSON.
- **Outputs:** JSON content per the A2A Agent Card schema.
- **Business Rules:**
  - When agent missing → 404.
  - When `status = "stopped"` → 503 with `{"error": "agent_stopped"}`.
  - When `status = "started"` → 200 with the card JSON.
  - The card body MUST equal:
    ```json
    {
      "name": "<agent.name>",
      "description": "<agent.description>",
      "version": "1.0",
      "url": "<absolute URL>/agents/<id>",
      "capabilities": {"streaming": true, "pushNotifications": false},
      "securitySchemes": {"bearer_api_key": {"type": "http", "scheme": "bearer"}},
      "skills": [{"name": "<agent.name>", "description": "<agent.description>", "tags": ["<mode>"]}],
      "defaultInputModes": ["text"],
      "defaultOutputModes": ["text"]
    }
    ```

### FR-012: API key authentication

See "Auth" above. Constant-time comparison REQUIRED.

### FR-013: 503 for stopped agents

See "503" above.

### FR-014: Simple mode executor

- **Description:** Loop only on tool calls; emit a final answer when the LLM stops calling tools.
- **Business Rules:**
  - Hard cap on tool-call iterations: 50.
  - Tool retry per FR-018.
  - Per-context memory per FR-021.
  - LLM call MUST receive `tools` in OpenAI-tools format derived from the agent's MCP `tools_cache`.

### FR-018: MCP tool retry policy

See "Tool retry" above.

### FR-021: Per-context conversation memory

See "Per-context memory caps" above.

### FR-022: Token / cost tracking

- After each LLM response, record `tokens_in` (`usage.prompt_tokens`), `tokens_out` (`usage.completion_tokens`), and `cost_usd` (`usage.cost` for OpenRouter; NULL for openai_compatible).

### FR-024: OpenTelemetry JSONL export

- Spans emitted by this story include `a2a.request`, `db.<op>`, `mcp.<op>`, `llm.chat`, `executor.simple.iter`.
- LLM span attributes per the redaction rules above.

## Acceptance Tests

> Acceptance tests are mandatory: 100% must pass via `make test`. Loop until green.

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `respx`-mocked LLM | Configurable mock for OpenRouter / OpenAI-compatible chat completions. Each test specifies the response payloads. | auto-generated | ready |
| `respx`-mocked OpenRouter cost field | Mock includes `usage: {prompt_tokens, completion_tokens, cost}`. | auto-generated | ready |
| Fake stdio MCP fixture (from US-004) | Reused; new fixture `fake-mcp-add` exposes a tool `add(a:int, b:int)` and a tool `flaky` that always errors. | auto-generated | ready |
| `start_agent(agent_id)` test helper | Calls POST `/agents/<id>/start` as the owner. | auto-generated | ready |
| `issue_key(agent_id)` test helper | Returns the plain key by retaining it from the create-agent flow (US-002). | auto-generated | ready |
| Tracer-capture fixture | Configures the OTel JSONL exporter to write to a `tmp_path` file; provides `read_spans()` returning a list of decoded JSON objects. | auto-generated | ready |
| `make_a2a_request` helper | Builds a minimal `SendStreamingMessage` envelope with `message: {parts: [{kind: "text", text: <s>}]}` and optional `contextId`. | auto-generated | ready |
| `parse_sse(stream)` helper | Iterates an SSE response, yielding parsed event JSON objects. | auto-generated | ready |
| `mock_clock_or_perf_counter` | A fixture allowing one test (E2E-095) to compare elapsed timings deterministically. | auto-generated | ready |

### Happy Path Tests

#### E2E-025 (full): Start an agent makes the route active

- **Category:** happy / state
- **Scenario:** SC-005
- **Requirements:** FR-006 (from US-003), FR-011
- **Preconditions:** Alice owns `A` with `status = "stopped"`.
- **Steps:**
  - When Alice POSTs `/agents/<A_id>/start`.
  - Then `agents.status` becomes `"started"`.
  - And a subsequent GET `/agents/<A_id>/.well-known/agent-card.json` returns 200 with `Content-Type: application/json`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-048: Simple mode A2A end to end (no tools)

- **Category:** happy
- **Scenario:** SC-009
- **Requirements:** FR-010, FR-014
- **Preconditions:**
  - Started Simple agent `A` with no MCP servers attached.
  - LLM mock returns a single assistant message with no tool call: content `"Hello, world."`. `usage: {prompt_tokens: 10, completion_tokens: 5, cost: 0.0001}`.
  - Issued API key `K` for `A`.
- **Steps:**
  - When the A2A client POSTs `/agents/<A_id>` with `Authorization: Bearer K`, body a SendStreamingMessage with TextPart `"Hi"`.
  - Then response is `text/event-stream` with HTTP 200.
  - And the SSE stream contains, in order: a `TaskStatusUpdate {state: "working"}`, one or more `TaskArtifactUpdate` whose concatenated text equals `"Hello, world."`, a final `TaskStatusUpdate {state: "completed"}`.
  - And `agent_runs` has exactly one row with `status="completed"`, `loops=1`, `tokens_in=10`, `tokens_out=5`, `final_answer="Hello, world."`.
  - And `agent_messages` for the new context contains rows: user, assistant.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-049: Simple mode with one tool call

- **Category:** happy
- **Scenario:** SC-009
- **Requirements:** FR-014, FR-021
- **Preconditions:**
  - Started Simple agent `A` with one stdio MCP server exposing tool `add(a: int, b: int) -> int` (the `fake-mcp-add` fixture).
  - LLM mock turn 1: assistant with a tool call `add(a=2, b=3)`. Turn 2 (after tool result `5`): assistant text `"The answer is 5"`. Per-turn `usage: {prompt_tokens: 5, completion_tokens: 5, cost: 0.0001}`.
- **Steps:**
  - When the A2A client sends `"What is 2+3?"`.
  - Then the SSE stream ends with `TaskStatusUpdate {state: "completed"}` and a final artifact carrying `"The answer is 5"`.
  - And the MCP fixture recorded exactly one call to `add` with args `{a: 2, b: 3}`.
  - And `agent_run_steps` for the run contains rows in order: `assistant` (with tool_call), `tool` (with `tool_result_json` content `5`), `assistant` (final).
  - And `agent_messages` for the new context contains 4 rows in order: user, assistant (with tool_call_json), tool, assistant.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-086: Agent Card returned for a started agent

- **Category:** happy
- **Scenario:** SC-012
- **Requirements:** FR-011
- **Preconditions:**
  - A started agent `A` named `fin-analyst`, mode `react`, with MCP server cache containing `[search]`. The `react` choice is intentional to assert the `tags` field carries the mode value.
- **Steps:**
  - When the client GETs `/agents/<A_id>/.well-known/agent-card.json`.
  - Then response status is 200 with `Content-Type: application/json`.
  - And the parsed JSON body equals (deep equal):
    ```json
    {
      "name": "fin-analyst",
      "description": "<A.description>",
      "version": "1.0",
      "url": "<absolute>/agents/<A_id>",
      "capabilities": {"streaming": true, "pushNotifications": false},
      "securitySchemes": {"bearer_api_key": {"type": "http", "scheme": "bearer"}},
      "skills": [{"name": "fin-analyst", "description": "<A.description>", "tags": ["react"]}],
      "defaultInputModes": ["text"],
      "defaultOutputModes": ["text"]
    }
    ```
- **Cleanup:** Truncate.
- **Priority:** Critical

### Edge Case and Error Tests

#### E2E-026: Stop returns 503 to A2A clients on subsequent POST

- **Category:** failure
- **Scenario:** SC-005
- **Requirements:** FR-013
- **Preconditions:**
  - Started agent `A`, key `K`. The client subsequently POSTs `/agents/<A_id>/stop`.
- **Steps:**
  - When the A2A client POSTs `/agents/<A_id>` with `Authorization: Bearer K` and a minimal SendStreamingMessage.
  - Then response status is 503 with body JSON exactly `{"error": "agent_stopped"}`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-028: Stopping does not interrupt in-flight runs

- **Category:** state transition
- **Scenario:** SC-005
- **Requirements:** FR-006, FR-013
- **Preconditions:**
  - Started Simple agent `A` with no MCP. LLM mock stalls 2 seconds before responding with a final assistant message `"done"`.
- **Steps:**
  - Given client X starts an A2A request that enters the LLM stall.
  - When the user POSTs `/agents/<A_id>/stop` while X is still streaming.
  - Then X's run completes normally: final SSE event is `TaskStatusUpdate {state: "completed"}` and `agent_runs.status = "completed"`.
  - And a NEW POST to `/agents/<A_id>` started after the stop returns HTTP 503.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-029: 503 body equals `{"error": "agent_stopped"}`

- **Category:** side effect
- **Scenario:** SC-005
- **Requirements:** FR-013
- **Steps:**
  - Given a stopped agent and a valid key.
  - When the A2A client POSTs `/agents/<A_id>`.
  - Then response status is 503 with body equal to the exact JSON `{"error": "agent_stopped"}`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-033: Delete cancels in-flight runs

- **Category:** side effect
- **Scenario:** SC-006
- **Requirements:** FR-005, FR-014 (cancellation)
- **Preconditions:**
  - Started Simple agent `A`. Client X has an A2A request stalled in the LLM mock.
- **Steps:**
  - When Alice deletes `A` (POST with `_method=DELETE`).
  - Then within 5 seconds X's SSE stream ends with a final `TaskStatusUpdate {state: "cancelled"}`.
  - And `SELECT count(*) FROM agent_runs WHERE id = <X_run_id>` returns 0 (cascade-deleted).
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-042: MCP tool list is presented in OpenAI tools format to the LLM

- **Category:** edge
- **Scenario:** SC-007 / SC-009
- **Requirements:** FR-008, FR-014
- **Preconditions:**
  - Started Simple agent `A` with one MCP server cached as `{tools: [{name: "search", description: "Web search", input_schema: {type: "object", properties: {query: {type: "string"}}, required: ["query"]}}]}`.
  - LLM mock returns a final assistant message immediately (no tool call).
- **Steps:**
  - When the A2A client triggers a run on `A`.
  - Then the LLM mock recorded the request payload, and the request `tools` array equals (deep equal):
    ```json
    [{"type": "function", "function": {"name": "search", "description": "Web search", "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]
    ```
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-050: A2A POST without Bearer returns 401

- **Category:** failure
- **Scenario:** SC-009
- **Requirements:** FR-012
- **Steps:**
  - Given a started agent `A`.
  - When the A2A client POSTs `/agents/<A_id>` with NO `Authorization` header and a minimal envelope.
  - Then response status is 401 with body `{"error": "auth_required"}` and `Content-Type: application/json`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-051: A2A POST with key from another agent returns 403

- **Category:** failure
- **Scenario:** SC-009
- **Requirements:** FR-012
- **Steps:**
  - Given two started agents `A1` (key `K1`) and `A2` (key `K2`).
  - When the client POSTs `/agents/<A1_id>` with `Authorization: Bearer K2` and a minimal envelope.
  - Then response status is 403 with body `{"error": "auth_invalid"}`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-052: LLM returns 5xx → A2A task ends `failed`

- **Category:** failure
- **Scenario:** SC-009
- **Requirements:** FR-014
- **Preconditions:**
  - Started Simple agent. LLM mock returns HTTP 500 once (no LLM-layer retries are part of the spec for the chat call).
- **Steps:**
  - When the A2A client sends a message.
  - Then the SSE stream ends with `TaskStatusUpdate {state: "failed", reason: "llm_provider_error"}`.
  - And `agent_runs.status = "failed"`, `stop_reason = "llm_provider_error"`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-053: Tool call fails 3× → tool_result with error returned to LLM

- **Category:** failure
- **Scenario:** SC-009
- **Requirements:** FR-018
- **Preconditions:**
  - Started Simple agent with MCP tool `flaky` that always returns an error envelope.
  - LLM mock turn 1: tool call `flaky()`. Turn 2 (after error tool_result): final text `"tool failed"`.
- **Steps:**
  - When the client sends a message.
  - Then the MCP fixture recorded exactly 3 calls to `flaky` (1 + 2 retries) before the error was surfaced to the LLM.
  - And `agent_runs.status = "completed"` (Simple mode keeps going after a failed tool).
  - And `agent_run_steps` includes a `tool` step with `tool_result_json.is_error == true`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-054: Simple defensive cap stops at 50 tool iterations

- **Category:** failure
- **Scenario:** SC-009
- **Requirements:** FR-014, FR-017
- **Preconditions:**
  - Started Simple agent with MCP tool `noop`. LLM mock ALWAYS returns a tool call (never finalizes).
- **Steps:**
  - When the client sends a message.
  - Then the run terminates with `agent_runs.status = "failed"`, `stop_reason = "guardrail_exceeded"` after exactly 50 tool iterations.
  - And the MCP fixture recorded exactly 50 calls to `noop`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-055: Reusing a `contextId` reuses prior messages

- **Category:** side effect
- **Scenario:** SC-009
- **Requirements:** FR-021
- **Preconditions:**
  - An existing context `C` for agent `A` with prior assistant message `"Last answer was 5"`.
- **Steps:**
  - When the client sends a new message with `contextId = C` and text `"Why?"`.
  - Then the LLM mock recorded a request whose `messages` array begins with all prior persisted messages for `C` (in `position` order) and ends with the new user message `"Why?"`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-056: OpenRouter cost is recorded from response

- **Category:** side effect
- **Scenario:** SC-009
- **Requirements:** FR-022
- **Preconditions:**
  - Started Simple OpenRouter agent. LLM mock returns `usage: {prompt_tokens: 100, completion_tokens: 200, cost: 0.0042}`.
- **Steps:**
  - When the client sends a message.
  - Then `agent_runs.tokens_in = 100`, `tokens_out = 200`, `cost_usd = 0.0042` (compare with `Decimal('0.0042')`).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-057: Trace JSONL never contains the prompt or response text

- **Category:** side effect / security
- **Scenario:** SC-009
- **Requirements:** FR-024
- **Preconditions:**
  - The OTel JSONL exporter is wired to `tmp_path / "trace.jsonl"`.
- **Steps:**
  - Given a Simple agent run where the user message text contains the unique substring `unique-secret-PROMPT-token-XYZ` and the LLM mock's assistant content contains `unique-secret-RESPONSE-token-ABC`.
  - When the run completes.
  - Then `trace.jsonl` exists and contains at least one span whose `name` starts with `llm.chat`.
  - And `grep -c "unique-secret-PROMPT-token-XYZ" trace.jsonl` returns 0 (test reads the file and asserts the substring is absent).
  - And `grep -c "unique-secret-RESPONSE-token-ABC" trace.jsonl` returns 0.
  - And the `llm.chat` span attributes include exactly the keys `model`, `provider`, `tokens.prompt`, `tokens.completion`, `cost.usd`, `duration.ms`, `request_id` and no others.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-058: Soft message cap drops oldest non-system messages from LLM payload

- **Category:** edge
- **Scenario:** SC-009
- **Requirements:** FR-021
- **Preconditions:**
  - A context `C` with 60 prior non-system messages (positions 1..60) plus 1 system message (position 0).
- **Steps:**
  - When the client sends a new message on `C`.
  - Then the LLM mock recorded a request whose `messages` array length is at most 50 AND includes the system message AND the new user message.
  - And the dropped messages are the OLDEST non-system messages (lowest `position` values among the non-system rows).
  - And `SELECT count(*) FROM agent_messages WHERE context_id = <C>` after the run still returns 62 (60 prior + 1 system + the new user message + the new assistant message; the DB retains everything; the test verifies the soft drop is payload-only).

  > Note: depending on whether the assistant turn is appended to the DB, the post-run count is 62 or 61. The contract is "DB retains all 61 prior messages plus any new turns; ONLY the LLM payload is capped at 50." Test SHOULD assert: count of prior 61 rows is unchanged (`>= 61`), AND the LLM payload had ≤ 50 messages.

- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-059: Hard message cap rejects new appends with `context_full`

- **Category:** edge
- **Scenario:** SC-009
- **Requirements:** FR-021
- **Preconditions:**
  - A context with exactly 1000 messages.
- **Steps:**
  - When the client sends a new message on that context.
  - Then the SSE stream ends with `TaskStatusUpdate {state: "failed", reason: "context_full"}`.
  - And no new `agent_messages` rows are inserted (count remains 1000).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-087: Agent Card returns 503 when stopped

- **Category:** failure
- **Scenario:** SC-012
- **Requirements:** FR-013
- **Steps:**
  - Given a stopped agent.
  - When the client GETs the Agent Card URL.
  - Then response status is 503 with body equal to JSON `{"error": "agent_stopped"}`.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-088: Agent Card returns 404 for unknown agent

- **Category:** failure
- **Scenario:** SC-012
- **Requirements:** FR-011
- **Steps:**
  - When the client GETs `/agents/<random_uuid>/.well-known/agent-card.json`.
  - Then response status is 404.
- **Cleanup:** None.
- **Priority:** High

#### E2E-089: Anonymous POST to a stopped agent returns 401, not 503

- **Category:** edge / security
- **Scenario:** SC-012
- **Requirements:** FR-012, FR-013
- **Steps:**
  - Given a stopped agent.
  - When the A2A client POSTs `/agents/<A_id>` WITHOUT a Bearer token.
  - Then response status is 401 (auth checked first; status not leaked to anonymous callers).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-090: Editing an agent mid-run does not affect the in-flight run

- **Category:** cross scenario
- **Scenario:** SC-004 + SC-009
- **Requirements:** FR-004 + FR-014 (snapshot)
- **Preconditions:**
  - Started Simple agent `A` with `system_prompt = "OLD"`. Client X has an in-flight run; the LLM mock is stalled on the first chat call. The run snapshotted `agent_runs.config_snapshot` at start with `system_prompt = "OLD"`.
- **Steps:**
  - When Alice edits `A` setting `system_prompt = "NEW"` (US-003 path).
  - Then on unblocking the LLM mock, the captured request body's first system message text equals `"OLD"` (from `config_snapshot`), NOT `"NEW"`.
  - And a NEW run started after the edit captures a fresh `config_snapshot` whose `system_prompt = "NEW"`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-091: Deleting an agent mid-run cancels and cleans up

- **Category:** cross scenario
- **Scenario:** SC-006 + SC-009
- **Requirements:** FR-005
- **Steps:** Same shape as E2E-033; in addition, asserts the SSE final event JSON deep-equals `{"event": "TaskStatusUpdate", "state": "cancelled"}`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-092: Stop mid-run lets the run finish; new run gets 503

- **Category:** cross scenario
- **Scenario:** SC-005 + SC-009
- **Requirements:** FR-006, FR-013
- **Steps:** Same as E2E-028.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-093: Two clients calling the same agent concurrently isolate contexts

- **Category:** cross scenario
- **Scenario:** SC-009 (×2)
- **Requirements:** FR-010, FR-021
- **Preconditions:**
  - Started Simple agent `A`. LLM mock returns a final assistant message immediately. Two existing contexts `C1` and `C2` each with one prior assistant message (different texts).
- **Steps:**
  - When client 1 sends a message with `contextId = C1` AND client 2 sends a message with `contextId = C2`, fired concurrently (`asyncio.gather`).
  - Then both runs reach `state = "completed"`.
  - And after the runs, `agent_messages` for `C1` contains only client 1's added user/assistant messages alongside its prior message (no client-2 content); same shape for `C2`.
  - And the LLM mock's two captured requests have the prior message of `C1` in request 1 and the prior message of `C2` in request 2 (no cross-context contamination).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-094: Cross-user isolation on POST /agents/<id> via API key

- **Category:** security
- **Scenario:** (multi)
- **Requirements:** FR-012
- **Steps:** Same as E2E-051 (recorded under a separate id for the security suite traceability).
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-095: Constant-time API key comparison (no timing oracle)

- **Category:** security
- **Scenario:** (multi)
- **Requirements:** FR-012
- **Preconditions:**
  - Started agent `A` with a valid key `K`.
  - Two crafted invalid keys: `K_first_diff` (differs at byte 0 from `K`) and `K_last_diff` (differs at the last byte from `K`).
- **Steps:**
  - When the test sends 1000 requests alternating between `K_first_diff` and `K_last_diff` and records the wall-clock duration of each request.
  - Then both groups produce HTTP 401/403 responses.
  - And the median elapsed time of the `K_first_diff` group is within 2× of the median elapsed time of the `K_last_diff` group (loose bound: asserts `hmac.compare_digest` is in use; raw `==` would diverge by orders of magnitude on this many samples).
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-098: LLM trace redaction (consolidated)

- **Category:** security
- **Scenario:** (multi)
- **Requirements:** FR-024
- **Steps:** Same as E2E-057 (Simple-mode redaction). The ReAct embedding-trace redaction variant lives in US-006.
- **Cleanup:** Truncate.
- **Priority:** Critical

## Constraints

### Files Not to Touch

- All US-001..US-004 source files except where explicit hooks are required (e.g., the agent edit endpoint must populate `config_snapshot` only when triggered by a NEW run; the edit endpoint itself stays unchanged).

### Dependencies Not to Add

- Allowed: `mcp` (already in US-004), `httpx`, `respx`. Do NOT pull in any LLM SDK; build the OpenRouter / OpenAI-compatible call via `httpx` directly.

### Patterns to Avoid

- Do NOT use `==` for API key comparison. Use `hmac.compare_digest`.
- Do NOT include any prompt / response / tool I/O / header value / API key / Fernet key in OTel span attributes. Filter at the exporter boundary.
- Do NOT block the SSE event loop on database writes; persist `agent_run_steps` on a non-blocking task or batch.
- Do NOT skip cancellation checks; check the cancel token at every awaitable boundary in the executor.

### Scope Boundary

- ReAct mode (FR-015), guardrails-with-synthesis (FR-017 except the simple defensive 50-iteration cap), embedding retry (FR-019), Plan & Execute (FR-016, FR-020) are NOT in this story.
- The Runs UI page (FR-009 UI; SC-008 tests E2E-043..047) is NOT in this story; data is persisted, but the user-facing page lives in US-008.
- File parts in A2A messages: only TextPart is required for these tests; FilePart handling can be a no-op (passed through to the LLM as text or ignored) — implement only what the tests exercise.

## Non Regression

### Existing Tests That Must Pass

- All US-001..US-004 tests, in particular:
  - The cascade-delete test from US-003 (now exercises real `agent_runs`, `agent_run_steps`, `agent_contexts`, `agent_messages` rows in addition to the empty fixture rows).
  - The MCP discovery flow (US-004).
  - Provider API key encryption round trip.
  - Dashboard performance baseline (the new `agent_runs` schema must keep the aggregation query fast).

### Behaviors That Must Not Change

- Builder UI flows for create / edit / delete / start / stop remain unchanged from US-002 / US-003.
- Provider API key encryption format unchanged.
- Carbon CSS / theme tokens unchanged.

### API Contracts to Preserve

- `GET /agents`, `GET /agents/new`, `POST /agents`, `GET /agents/<id>`, `GET /agents/<id>/edit`, `POST /agents/<id>`, `POST /agents/<id>/start`, `POST /agents/<id>/stop`, `POST /agents/<id>` with `_method=DELETE`, `POST /agents/<id>/keys`, `GET|POST|DELETE /agents/<id>/mcp/...`.
