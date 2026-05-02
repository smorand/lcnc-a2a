# Agent edit, delete, start/stop lifecycle (US-003)

## Routes (`routes/agents.py`)

- `GET /agents/{id}/edit` — prefilled form. 404 if not owned. Provider API
  key field is empty with placeholder `********`.
- `POST /agents/{id}` — when form contains `_method=DELETE`, deletes the
  agent and 302-redirects to `/agents`. Otherwise validates and updates via
  `services.agents.update_agent`. 404 if not owned. The `_method` form field
  is bound via `Annotated[str, Form(alias="_method")]` (Pydantic forbids
  field names starting with `_`).
- `POST /agents/{id}/start` and `POST /agents/{id}/stop` — atomic
  `UPDATE agents SET status = ...`. Idempotent. 302 to `Referer` (or
  `/agents`).

All state-mutating handlers validate the CSRF token (`csrf_invalid` 403 on
mismatch).

## Validation differences vs create

`schemas.agent_form.validate_create_agent_form` accepts
`require_provider_api_key: bool = True`. The edit handler passes
`False`, so a blank `provider_api_key` field is accepted and means
"unchanged". The service `update_agent` only re-encrypts when the new
plaintext is non-empty; otherwise `provider_api_key_enc` is left
byte-for-byte identical (Fernet's randomized nonce never re-runs).

## Cascade delete

`services.agents.delete_agent_cascade` issues a single SQL DELETE on
`agents`; PostgreSQL `ON DELETE CASCADE` clauses on every dependent table
remove the rows transitively. No application-level cascade is needed.
Tables removed:

- `agent_api_keys` (FK `agent_id`)
- `agent_mcp_servers` (FK `agent_id`)
- `agent_runs` (FK `agent_id`)
- `agent_run_steps` (FK `run_id` → `agent_runs`)
- `agent_contexts` (FK `agent_id`)
- `agent_messages` (FK `context_id` → `agent_contexts`)

## Schema additions

Migration `0003_lifecycle_cascade_tables.py` creates the cascade-target
tables with their final schema (mirrors spec §8 `Database schema`). They
are populated by US-004 (`agent_mcp_servers`) and US-005 (run steps,
contexts, messages). Skeletal SQLAlchemy models live in
`src/lcnc_a2a/models/{agent_mcp_server,agent_context,agent_message,agent_run_step}.py`
so `Base.metadata.create_all` (used by tests) materializes them.

## Tests

- `tests/e2e/test_edit_agent.py` — E2E-020..024
- `tests/e2e/test_delete_agent.py` — E2E-030..034 (cascade verified by
  direct SQL inserts into all six dependent tables before delete)
- `tests/e2e/test_state_toggle.py` — E2E-025, E2E-027 plus extra
  cross-user 404 coverage

## Out of scope (deferred)

- Run cancellation on stop/delete: lands in US-005.
- Agent Card 503 vs 200 by status: US-005.
- POST `/agents/{id}` (A2A SendStreamingMessage): US-005.
