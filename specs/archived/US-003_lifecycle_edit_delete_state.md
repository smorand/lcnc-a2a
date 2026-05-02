# US-003: Agent edit, delete, start/stop lifecycle

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 3
> Depends On: US-002
> Complexity: M

## Objective

Complete the agent lifecycle: an authenticated user can edit any field of their own agents (with provider API key re-encryption when supplied), toggle the `started`/`stopped` state idempotently, and delete an agent with cascade across all related rows. Cross-user access returns HTTP 404 (existence is not leaked). After this story the dashboard's CRUD is fully operational; runtime A2A behaviors (in-flight runs, Agent Card responses) ship in US-005.

## Technical Context

### Stack

Same as US-002.

### Relevant File Structure

```
src/lcnc_a2a/
├── routes/
│   └── agents.py                # adds GET /agents/<id>/edit, POST /agents/<id>, POST /agents/<id>/start, POST /agents/<id>/stop, POST /agents/<id> with _method=DELETE
├── services/
│   └── agents.py                # update_agent(), delete_agent_cascade(), set_status()
├── templates/
│   └── agents/
│       ├── edit.html
│       └── partials/
│           └── delete_confirm.html
└── tests/e2e/
    ├── test_edit_agent.py
    ├── test_delete_agent.py
    └── test_state_toggle.py

alembic/versions/
└── 0003_lifecycle_indexes.py    # any indexes needed for cascade performance (optional)
```

### Existing Patterns

- `agents` table FKs cascade in DDL; nothing application-level needed for cascade.
- Method override via form field `_method=DELETE` is parsed in a single helper (similar pattern to common Flask/FastAPI conventions).
- Edit form is the same template as create (US-002), prefilled, with the provider API key field rendered as a masked `********` placeholder. Empty submission means "unchanged".

### Data Model (excerpt)

The cascade target tables (created here as empty in this story; populated in US-004 and US-005):

| Table | FK to agents.id | ON DELETE |
|---|---|---|
| agent_api_keys | yes | CASCADE |
| agent_mcp_servers | yes (created in US-004) | CASCADE |
| agent_runs | yes | CASCADE |
| agent_run_steps | via agent_runs | CASCADE |
| agent_contexts | yes | CASCADE |
| agent_messages | via agent_contexts | CASCADE |

For US-003 the cascade is verified by inserting **fixture rows** into each table directly via SQL (the tables exist as empty schemas pre-created here if they don't already exist) and checking that DELETE on `agents` removes them. Tables that won't be created until later stories (`agent_mcp_servers`, `agent_run_steps`, `agent_contexts`, `agent_messages`) MUST be created in this story's migration with their full final schema so the cascade test is possible without circular dependencies.

## Functional Requirements

### FR-004: Edit an agent

- **Description:** GET `/agents/<id>/edit` renders the prefilled form. POST `/agents/<id>` updates the agent.
- **Inputs:** All FR-003 fields. `provider_api_key` left blank means "unchanged".
- **Outputs:** HTTP 302 to `/agents/<id>` with a flash message; or 400 with field errors.
- **Business Rules:**
  - The owner check uses `agents.user_id = current_user.id`. Mismatched / unknown id returns HTTP 404 (do NOT leak existence).
  - When `provider_api_key` is left blank, `provider_api_key_enc` MUST be byte-equal to its prior value.
  - When `provider_api_key` is supplied, `provider_api_key_enc` MUST decrypt to the new value AND its bytes MUST differ from the prior value (Fernet's nonce ensures this even if the plaintext is unchanged, but tests assert the new plaintext explicitly).
  - Mode change to `plan_execute` requires both `planner_prompt` and `executor_prompt` non-empty; failure to provide returns 400 with `prompts_required` and the agent's mode unchanged.
  - `updated_at` MUST advance on every successful update.
  - Edits do NOT affect any in-flight run (the run snapshots config at start; that snapshotting lives in US-005).

### FR-005: Delete an agent

- **Description:** POST `/agents/<id>` with `_method=DELETE` (or HTTP DELETE `/agents/<id>`) removes the agent.
- **Inputs:** Path id, session cookie.
- **Outputs:** HTTP 302 to `/agents`.
- **Business Rules:**
  - Owner check identical to FR-004.
  - DDL ON DELETE CASCADE removes all dependent rows in `agent_api_keys`, `agent_mcp_servers`, `agent_runs`, `agent_run_steps`, `agent_contexts`, `agent_messages`.
  - In-flight run cancellation is NOT in scope here; that lands in US-005 (the cancellation token plumbing requires the executor).
  - Deleting an unknown id returns HTTP 404.

### FR-006: Toggle agent state (start / stop)

- **Description:** POST `/agents/<id>/start` sets `status = "started"`. POST `/agents/<id>/stop` sets `status = "stopped"`.
- **Inputs:** Path id, session cookie.
- **Outputs:** HTTP 302 back to the `Referer` (or `/agents` if absent).
- **Business Rules:**
  - State change is a single atomic UPDATE.
  - Idempotent: starting a started agent or stopping a stopped agent is a no-op (no error, status unchanged, 302 anyway).
  - In-flight runs are NOT interrupted by stop (this is enforced at the executor level in US-005; in US-003 the toggle merely flips the column).
  - Mismatched owner returns HTTP 404.

## Acceptance Tests

> Acceptance tests are mandatory: 100% must pass via `make test`. Loop until green.

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| `direct_insert(table, **cols)` SQL helper | Inserts arbitrary rows into the cascade-target tables for verification of FR-005 cascade behavior. | auto-generated | ready |
| `seed_agent(user, **overrides)` helper | Builds an agent row with sane defaults from US-002. | auto-generated | ready |
| Two-user setup | Reused from US-002 conftest. | auto-generated | ready |

### Happy Path Tests

#### E2E-020: Edit agent happy path (system prompt, blank API key)

- **Category:** happy
- **Scenario:** SC-004
- **Requirements:** FR-004
- **Preconditions:**
  - Alice owns agent `A` with `system_prompt = "old"` and a known `provider_api_key_enc` byte value `B_old`.
- **Steps:**
  - When Alice POSTs `/agents/<A_id>` with `system_prompt = "new"`, `provider_api_key = ""` (blank), and other fields unchanged.
  - Then response status is 302 with `Location: /agents/<A_id>`.
  - And `agents.system_prompt` for `A` equals exactly `"new"`.
  - And `agents.provider_api_key_enc` for `A` equals byte-for-byte `B_old` (unchanged).
  - And `updated_at` strictly advanced compared to its pre-edit value.
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-025: Start an agent flips status to "started"

- **Category:** happy / state
- **Scenario:** SC-005
- **Requirements:** FR-006
- **Preconditions:**
  - Alice owns agent `A` with `status = "stopped"`.
- **Steps:**
  - When Alice POSTs `/agents/<A_id>/start`.
  - Then response status is 302.
  - And `agents.status` for `A` is `"started"`.
- **Cleanup:** Truncate.
- **Priority:** Critical

> Note: the spec's E2E-025 also asserts that `/agents/<id>/.well-known/agent-card.json` returns 200 after start. That second assertion is deferred to US-005 where the Agent Card endpoint is implemented; in US-003 we verify only the status flip, which is the FR-006 contract.

#### E2E-030: Delete agent happy path with cascade

- **Category:** happy
- **Scenario:** SC-006
- **Requirements:** FR-005
- **Preconditions:**
  - Alice owns agent `A`.
  - The test directly inserts: 2 `agent_api_keys` rows, 1 `agent_mcp_servers` row, 3 `agent_runs` rows (each with 2 `agent_run_steps`), 2 `agent_contexts` (each with 2 `agent_messages`).
- **Steps:**
  - When Alice POSTs `/agents/<A_id>` with form field `_method=DELETE`.
  - Then response status is 302 with `Location: /agents`.
  - And `SELECT count(*) FROM agents WHERE id = <A_id>` returns 0.
  - And in EACH of the tables `agent_api_keys`, `agent_mcp_servers`, `agent_runs`, `agent_run_steps`, `agent_contexts`, `agent_messages`, the count of rows transitively belonging to `A` is 0.
- **Cleanup:** Truncate.
- **Priority:** Critical

### Edge Case and Error Tests

#### E2E-021: Edit returns 404 for another user's agent

- **Category:** failure / security
- **Scenario:** SC-004
- **Requirements:** FR-004
- **Preconditions:**
  - Alice owns agent `A`. Bob is logged in.
- **Steps:**
  - When Bob POSTs `/agents/<A_id>` with valid form fields.
  - Then response status is 404.
  - And `agents.system_prompt` for `A` is unchanged compared to before the request.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-022: Edit rejects mode change to PE without prompts

- **Category:** failure
- **Scenario:** SC-004
- **Requirements:** FR-004
- **Preconditions:**
  - Alice owns agent `A` with `mode = "react"` and `planner_prompt = NULL`, `executor_prompt = NULL`.
- **Steps:**
  - When Alice POSTs `/agents/<A_id>` with `mode = "plan_execute"`, `planner_prompt = ""`, `executor_prompt = ""`.
  - Then response status is 400 and the body contains the literal `prompts_required`.
  - And `agents.mode` for `A` is still `"react"`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-023: Edit replacing provider API key re-encrypts

- **Category:** side effect
- **Scenario:** SC-004
- **Requirements:** FR-023
- **Preconditions:**
  - Alice owns agent `A` with `provider_api_key = "K1"` (encrypted to `B1`).
- **Steps:**
  - When Alice POSTs `/agents/<A_id>` with `provider_api_key = "K2"` and other fields unchanged.
  - Then `agents.provider_api_key_enc` for `A` decrypts via the configured Fernet key to exactly `"K2"`.
  - And the new BYTEA value is NOT equal to `B1` (byte-different, since both Fernet ciphertext and plaintext changed).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-024: Edit preserves run history

- **Category:** edge
- **Scenario:** SC-004
- **Requirements:** FR-004, FR-009 (data preservation)
- **Preconditions:**
  - Alice owns agent `A` with 5 directly-inserted `agent_runs` rows (status `completed`).
- **Steps:**
  - When Alice edits `A` (e.g., updates `system_prompt`).
  - Then `SELECT count(*) FROM agent_runs WHERE agent_id = <A_id>` still returns 5.
  - And every prior run's row is byte-identical to its pre-edit content (test compares the rows by serializing them).
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-027: started → started and stopped → stopped are idempotent

- **Category:** state transition
- **Scenario:** SC-005
- **Requirements:** FR-006
- **Preconditions:**
  - Alice owns agent `A` with `status = "started"`.
- **Steps:**
  - When Alice POSTs `/agents/<A_id>/start` again.
  - Then response status is 302 (no error).
  - And `agents.status` is still `"started"`.
  - When Alice POSTs `/agents/<A_id>/stop` then immediately POSTs `/stop` again.
  - Then both responses are 302.
  - And `agents.status` is `"stopped"` (single atomic UPDATE applied each time).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-031: Delete returns 404 for another user's agent

- **Category:** failure
- **Scenario:** SC-006
- **Requirements:** FR-005
- **Preconditions:**
  - Alice owns agent `A`. Bob is logged in.
- **Steps:**
  - When Bob POSTs `/agents/<A_id>` with `_method=DELETE`.
  - Then response status is 404.
  - And `SELECT count(*) FROM agents WHERE id = <A_id>` still returns 1.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-032: Delete unknown agent returns 404

- **Category:** failure
- **Scenario:** SC-006
- **Requirements:** FR-005
- **Preconditions:**
  - Alice is logged in.
- **Steps:**
  - When Alice POSTs `/agents/<random-uuid>` with `_method=DELETE` (no row with that id exists).
  - Then response status is 404.
- **Cleanup:** None.
- **Priority:** Medium

#### E2E-034: Delete cascade removes ALL related rows (data integrity)

- **Category:** data integrity
- **Scenario:** SC-006
- **Requirements:** FR-005
- **Preconditions:**
  - Same fixture as E2E-030 (agent + cascade-target rows).
- **Steps:**
  - When Alice deletes the agent.
  - Then for EACH of `agent_api_keys`, `agent_mcp_servers`, `agent_runs`, `agent_run_steps`, `agent_contexts`, `agent_messages`, the SQL `SELECT count(*)` filtered transitively by the deleted `agent_id` returns 0.
- **Cleanup:** Truncate.
- **Priority:** Critical

## Constraints

### Files Not to Touch

- Auth, settings, encryption, base templates from US-001.
- The `agents` model and `agents` table schema from US-002 (this story EXTENDS it via migrations only when adding cascade target tables for the cascade test).

### Dependencies Not to Add

- No new runtime dependencies.

### Patterns to Avoid

- Do NOT implement application-level cascade DELETE; rely on PostgreSQL ON DELETE CASCADE.
- Do NOT add `Cancel running A2A run` semantics here; that depends on the executor and lives in US-005.
- Do NOT couple the start/stop endpoints to the A2A endpoint logic; they only flip a column.

### Scope Boundary

- The Agent Card response (200 when started, 503 when stopped) is NOT in this story.
- POST `/agents/<id>` (the A2A SendStreamingMessage endpoint) is NOT in this story.
- In-flight run cancellation behavior is NOT in this story (E2E-028, E2E-033 belong to US-005).

## Non Regression

### Existing Tests That Must Pass

- All US-001 and US-002 tests.

### Behaviors That Must Not Change

- Agent creation flow (US-002) and dashboard listing remain unchanged.
- Carbon CSS link and theme tokens remain unchanged.
- Provider API key encryption format (Fernet) remains unchanged.

### API Contracts to Preserve

- `GET /agents`, `GET /agents/new`, `POST /agents`, `GET /agents/<id>`, `POST /agents/<id>/keys` from US-002.
