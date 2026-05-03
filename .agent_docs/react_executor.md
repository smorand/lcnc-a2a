# ReAct executor (US-006)

## Loop shape (FR-015)

`executors/react.py::ReActExecutor` runs the Thought → Act → Observe loop:

1. Build the OpenAI `messages` payload from the persisted context plus a user
   role message containing the current scratchpad (concatenated `Thought:`,
   `Action:`, `Observation:` lines).
2. Call the LLM (no SDK; raw httpx via `LlmProvider`). Wrap each iteration
   under a `executor.react.iter` span and the chat call under `llm.chat`.
3. Parse the response (`parse_react_response`):
   - `tool_calls` present → continue (thought = response content stripped of a
     leading `Thought:` prefix).
   - No tool calls + content starts with `Final Answer:` → final.
   - Anything else → `parse_error` (records role=`error` step with content
     `parse_error`; counts toward `max_loops`).
4. For tool-call iterations the executor yields three `TaskStatusUpdate`
   events with `payload.phase` ∈ `{"thought", "action", "observation"}` and
   the iteration number.
5. The MCP tool invocation reuses `executors/base.py::invoke_mcp_tool`,
   keeping the FR-018 retry policy (`TOOL_RETRY_BACKOFFS = (0.2, 0.6, 1.8)`).
6. Final-answer iterations skip phase events; they emit a single
   `TaskArtifactUpdate` followed by `TaskStatusUpdate(completed)`.

`agent_runs.loops` equals the number of LLM iterations actually executed
(parse-error iterations included, synthesis call NOT included).

## Embedding similarity (FR-015 + FR-019)

Cosine similarity (`services/similarity.py::cosine_similarity`, pure Python)
is checked starting at iteration 2:

- At iter 2 the executor first embeds iter 1's text (caching the vector),
  then embeds iter 2's text. Subsequent iterations only embed the current
  text (the previous vector is reused from the cache).
- `similarity >= similarity_threshold` (inclusive comparison) stops the run
  with `stop_reason = "similarity"` and `final_answer = previous candidate`.
- `agent_run_steps.similarity_to_prev` is updated on the iteration's
  `thought` row when similarity is computed.
- Embedding model is resolved via
  `llm/embeddings.py::resolve_embedding_model`: agent override wins,
  otherwise `openai/text-embedding-3-small` for OpenRouter.

`llm/embeddings.py::embed` runs three attempts (1 + 2 retries) with backoffs
`200ms / 600ms / 1800ms`. Retries fire on transport / 5xx / 429; other 4xx
fail immediately. After three failures the executor finalizes the run as
`failed` with `stop_reason = "embedding_unavailable"`.

## Guardrails and synthesis (FR-017)

After every iteration the executor checks `loops >= max_loops` or
`total_tokens_out >= max_tokens`. On hit (without a final answer):

- `executors/synthesis.py::should_skip_synthesis` estimates
  `scratchpad_chars / 4` extra tokens. If `cumulative + estimate >
  max_tokens * 1.5`, the run finalizes as
  `failed`/`guardrail_exceeded_no_synthesis`. No synthesis LLM call is made.
- Otherwise `run_synthesis` performs ONE chat call with the scratchpad as
  context and no tools. Its content becomes the final answer; the run is
  `completed` with `stop_reason ∈ {"max_loops", "max_tokens"}` (whichever
  fired first). The synthesis tokens count toward the totals.

## OTel redaction (FR-024)

`observability/jsonl_exporter.py` adds an `llm.embed` allow-list mirror of
the existing `llm.chat` filter (`model`, `provider`, `tokens.prompt`,
`tokens.completion`, `cost.usd`, `duration.ms`, `request_id`). Embedding
input strings never reach the exporter.

## Tests

- `tests/e2e/test_a2a_react.py`: happy path, similarity stop, threshold
  exclusivity, parse_error counting, tool retry, loops counter, per-loop
  trace persistence (E2E-060, 061, 066, 068, 069, 070, 072).
- `tests/e2e/test_react_guardrails.py`: max_loops + max_tokens synthesis,
  overshoot heuristic skip, parse_error path (E2E-062, 063, 064, 067).
- `tests/e2e/test_react_embeddings.py`: 503 retry exhaustion (E2E-065).
- `tests/e2e/test_react_otel.py`: prompt / response / embedding-input
  redaction in JSONL trace (E2E-071).
- Test helpers: `tests/e2e/_react_helpers.py` provides `StubEmbedding`,
  `make_embedding`, `add_react_tool_call`, `add_final_answer`,
  `add_unparseable`, `seed_started_react_agent`, `encrypt_env`.
