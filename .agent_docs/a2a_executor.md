# A2A surface and Simple executor (US-005)

## Routes

- `POST /agents/<id>` is shared between the UI form handler and the A2A endpoint. The request is treated as A2A when either `Authorization` is set or the request `Content-Type` starts with `application/json`. The dispatch happens in `routes/agents.py::update_or_delete_agent` and delegates to `routes/a2a.py::handle_a2a_post`.
- `GET /agents/<id>/.well-known/agent-card.json` lives in `routes/a2a.py`. Returns 404 for unknown agents and 503 with `{"error": "agent_stopped"}` when `agents.status != "started"`.

## Auth ordering (FR-012, FR-013)

1. Parse `Authorization: Bearer <key>` (`auth/api_key.py::parse_bearer_header`). Missing → 401 `auth_required` (status not leaked to anonymous callers, even on stopped agents).
2. Match the key against non-revoked rows for the agent using `hmac.compare_digest`. The route iterates over every candidate without short-circuiting to avoid a presence/timing oracle.
3. Only after auth, check `agents.status`. Stopped → 503 `agent_stopped`.

## Simple executor

- `executors/simple.py::SimpleExecutor` runs one A2A request: it appends the user message, emits `TaskStatusUpdate(working)`, then loops up to `MAX_ITERATIONS = 50`.
- Each iteration: load all persisted messages (`services/messages.py::list_messages`), run them through the soft 50-cap payload builder (`build_llm_payload`), call the provider's `chat`. If the LLM returns no `tool_calls`, emit a final artifact + `TaskStatusUpdate(completed)`. Otherwise call each tool, persist `assistant` (with tool_call_json) + `tool` rows, and loop.
- Tool retries: 3 attempts total with backoffs `0.2s, 0.6s, 1.8s` (`TOOL_RETRY_BACKOFFS`). On final failure the executor still surfaces the error to the LLM as a tool result with `is_error=true` and lets the loop continue.
- Hard 1000-message cap: `messages_service.append_message` raises `ContextFullError` and the executor terminates `failed` with `stop_reason = "context_full"`.
- LLM 5xx / network errors raise `LlmProviderError`; the executor finalizes the run as `failed` with `stop_reason = "llm_provider_error"` and emits `TaskStatusUpdate(failed, reason=...)`.
- The 50-iteration cap terminates `failed` with `stop_reason = "guardrail_exceeded"`.
- Cancellation: the registry hands the executor an `asyncio.Event`. The loop checks it before/after every LLM call and between tool calls; on cancel it finalizes the run as `cancelled` (or no-ops if the row is already gone via cascade) and emits `TaskStatusUpdate(cancelled)`.

## Snapshotting (FR-004 + FR-014)

`services/runs.py::create_run` captures `config_snapshot` JSON (system_prompt, mode, model_provider/endpoint/id, prompts, max_loops/max_tokens/max_steps, similarity_threshold) at run start. The Simple executor reads the prompt and limits from the snapshot, NOT the live `Agent` row. An edit issued mid-run cannot mutate the in-flight LLM payload.

## LLM provider abstraction

- `LlmProvider` ABC + `OpenRouterProvider` (records `usage.cost`) + `OpenAiCompatibleProvider` (`cost_usd = NULL`). Both built on raw `httpx.AsyncClient` (no LLM SDK).
- `llm/tool_format.py::to_openai_tools` translates an MCP `tools_cache` entry to the OpenAI `tools[]` shape used in the chat request.

## Cancellation registry

- `services/cancellation.py::CancellationRegistry` lives at `app.state.cancellation_registry`.
- `routes/a2a.py::handle_a2a_post` registers the event before yielding the first SSE event and unregisters in a `finally`.
- `routes/agents.py` delete path: before issuing the cascade DELETE, it calls `services/runs.py::list_running_run_ids_for_agent` and `registry.cancel_all_for_agent(...)` so any in-flight executor sees the event by its next checkpoint.

## OpenTelemetry redaction (FR-024)

- `observability/jsonl_exporter.py` applies a strict allow-list ONLY for spans whose name starts with `llm.chat`: keys are restricted to `model`, `provider`, `tokens.prompt`, `tokens.completion`, `cost.usd`, `duration.ms`, `request_id`. Anything else is dropped at export time.
- All other spans go through the default redaction (`api_key`, `authorization`, `password`, `secret`, `token`, `cookie`, `set-cookie`, `llm.prompt`, `llm.response` keys masked).

## Tests

- `test_a2a_card.py`: agent card surface (E2E-025, 086, 087, 088).
- `test_a2a_auth.py`: bearer auth + constant-time comparison (E2E-050, 051, 089, 094, 095).
- `test_a2a_simple.py`: SSE happy path, cost, soft/hard memory caps, llm 500 (E2E-026, 029, 048, 052, 055, 056, 058, 059).
- `test_a2a_tools.py`: tools format, single tool call, retry on flaky tool, 50-iter cap (E2E-042, 049, 053, 054).
- `test_a2a_lifecycle.py`: stop mid-run, delete cancellation, edit-during-run snapshot, concurrent contexts (E2E-028, 033, 090, 091, 092, 093).
- `test_a2a_otel.py`: prompt/response redaction, allow-list (E2E-057, 098).
- LLM mocking goes through `respx` against `https://openrouter.example.com/api/v1/chat/completions` (the seed helper sets the agent's `model_endpoint` accordingly).
- The `fake_mcp_add` fixture spawns a real `mcp` stdio server with `add`, `flaky`, `noop` tools; the touch-file env vars let tests count exact invocations.
