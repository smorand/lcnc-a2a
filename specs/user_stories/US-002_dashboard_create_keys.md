# US-002: Agent dashboard, agent creation, per-agent API keys

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 2
> Depends On: US-001
> Complexity: L

## Objective

Implement the agents dashboard listing the current user's agents with aggregated 30-day metrics, the agent creation form, and the per-agent API key generation flow with secrets-at-rest encryption. After this story, a logged-in user can create agents, see them listed on `/agents` with placeholder metrics (zero runs), and receive a single plaintext API key once at creation. Cross-user isolation and the dashboard performance baseline are also covered here.

## Technical Context

### Stack

Same as US-001 (Python 3.13, FastAPI, async SQLAlchemy, Jinja2 + HTMX, Carbon CSS).

### Relevant File Structure

```
src/lcnc_a2a/
├── models/
│   ├── agent.py               # Agent model (full schema from spec §8)
│   ├── agent_api_key.py       # AgentApiKey
│   └── agent_run.py           # AgentRun (minimal schema for aggregation queries)
├── routes/
│   ├── dashboard.py           # GET /agents (full implementation)
│   └── agents.py              # GET /agents/new, POST /agents, GET /agents/<id>, POST /agents/<id>/keys
├── services/
│   ├── agents.py              # CRUD + aggregation queries
│   └── api_keys.py            # generate / hash / fingerprint
├── schemas/
│   └── agent_form.py          # pydantic form validation
├── templates/
│   ├── agents/
│   │   ├── list.html
│   │   ├── new.html
│   │   ├── detail.html
│   │   └── partials/
│   │       ├── agent_row.html
│   │       └── api_key_once.html
└── tests/e2e/
    ├── test_dashboard.py
    ├── test_create_agent.py
    └── test_api_keys.py

alembic/versions/
└── 0002_agents_keys_runs.py    # creates agents, agent_api_keys, agent_runs tables + indexes
```

### Existing Patterns

- Reuse `AuthProvider` and session middleware from US-001.
- Reuse `crypto.encrypt/decrypt` from US-001 for `agents.provider_api_key_enc`.
- All template renders go through the base layout that links Carbon CSS (US-001).
- All POST forms include the CSRF token from US-001.

### Data Model (excerpt)

#### agents

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users.id ON DELETE CASCADE | |
| name | VARCHAR(120) | |
| description | TEXT | nullable, ≤ 2000 chars |
| mode | VARCHAR(20) | `simple` / `react` / `plan_execute` |
| model_provider | VARCHAR(40) | `openrouter` / `openai_compatible` |
| model_endpoint | VARCHAR(500) | LLM base URL |
| model_id | VARCHAR(200) | |
| provider_api_key_enc | BYTEA | Fernet-encrypted |
| embedding_model | VARCHAR(200) | nullable |
| system_prompt | TEXT | nullable |
| planner_prompt | TEXT | nullable |
| executor_prompt | TEXT | nullable |
| max_loops | INTEGER | |
| max_tokens | INTEGER | |
| similarity_threshold | DOUBLE PRECISION | nullable |
| max_steps | INTEGER | nullable |
| status | VARCHAR(20) | default `stopped` |
| created_at, updated_at | TIMESTAMPTZ | |

Unique index `(user_id, name)`. Plain index `(user_id)`.

#### agent_api_keys

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| agent_id | UUID FK → agents.id ON DELETE CASCADE | |
| label | VARCHAR(60) | default `default` |
| key_hash | BYTEA | sha256 raw bytes (32 bytes) |
| key_last4 | CHAR(4) | last 4 chars of plain key |
| created_at | TIMESTAMPTZ | |
| revoked_at | TIMESTAMPTZ | nullable |

Unique index on `key_hash`. Plain index on `agent_id`.

#### agent_runs (minimal schema needed by aggregation now; full schema lands in US-005)

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| agent_id | UUID FK → agents.id ON DELETE CASCADE | |
| status | VARCHAR(20) | |
| started_at | TIMESTAMPTZ | |
| duration_ms | INTEGER | nullable |
| loops | INTEGER | nullable |
| tokens_in | INTEGER | nullable |
| tokens_out | INTEGER | nullable |
| cost_usd | NUMERIC(12,6) | nullable |

Index `(agent_id, started_at DESC)`.

### Aggregation Rules (FR-002)

For each of the user's agents, compute over `agent_runs` WHERE `started_at >= now() - interval '<window> days'` (window via `LCNC_A2A_METRICS_WINDOW_DAYS`, default 30):

- `requests = count(*)`.
- `tokens_in = COALESCE(sum(tokens_in), 0)`.
- `tokens_out = COALESCE(sum(tokens_out), 0)`.
- `avg_duration_ms = AVG(duration_ms)` (NULL if no rows).
- `avg_loops = AVG(loops)` (NULL if no rows).
- `total_time_ms = COALESCE(sum(duration_ms), 0)`.
- `last_run_at = MAX(started_at)`.
- `total_cost_usd`: SUM only over runs where `cost_usd IS NOT NULL`. If ANY run in the window has `cost_usd IS NULL`, the dashboard MUST display the literal string `n/a` for that agent's cost cell.

## Functional Requirements

### FR-002: List user's agents with aggregated metrics

- **Description:** GET `/agents` returns the dashboard listing the current user's agents plus aggregated 30-day metrics.
- **Inputs:** Session cookie identifying the user.
- **Outputs:** HTML page with the agent table.
- **Business Rules:**
  - Cross-user isolation: query MUST filter by `agents.user_id = current_user.id`. No cross-user data may appear.
  - Empty-state: if the user has zero agents, render a Carbon empty state including the literal text `Create agent` and the hint about creating a first agent. CSS selector `tr.agent-row` MUST count 0.
  - Metrics over a 30-day window only; older runs are excluded.
  - Failed runs still contribute to counters per the spec (`tokens_in`, `tokens_out`, `requests`).
  - Performance: dashboard MUST render under 500 ms p95 with 50 seeded agents and 1000 historical runs (warm DB caches).

### FR-003: Create a new agent

- **Description:** GET `/agents/new` renders the form. POST `/agents` creates the agent owned by the current user.
- **Inputs:**
  - `name` (1..120 chars, unique per user).
  - `description` (0..2000 chars).
  - `mode` ∈ {`simple`, `react`, `plan_execute`}.
  - `model_provider` ∈ {`openrouter`, `openai_compatible`}.
  - `model_endpoint` (URL).
  - `model_id` (1..200 chars).
  - `provider_api_key` (string, encrypted at rest).
  - `system_prompt` (Simple/ReAct only, 1..40000 chars).
  - `planner_prompt` (PE only, 1..40000 chars).
  - `executor_prompt` (PE only, 1..40000 chars).
  - `max_loops` (1..50, default 10 for ReAct, 1 for PE synthesis).
  - `max_tokens` (100..200000, default 8000 Simple, 8000 ReAct, 16000 PE).
  - `similarity_threshold` (0.50..0.99, default 0.95, ReAct only).
  - `max_steps` (1..50, default 20, PE only).
- **Outputs:** HTTP 302 to `/agents/<id>`; the response page shows the generated API key in plain text exactly once with a copy button and a one-time warning.
- **Business Rules:**
  - Initial `status = "stopped"`.
  - Provider API key Fernet-encrypted in `agents.provider_api_key_enc`.
  - One API key labelled `default` is generated atomically with the agent (FR-007 sub-rule).
  - Validation errors (missing `name`, missing `model_id`, duplicate name, PE without prompts, `max_steps` out of range) return HTTP 400 with the form re-rendered AND the body containing the literal error code (e.g., `name_required`, `name_taken`, `prompts_required`, `max_steps_out_of_range`).

### FR-007: Generate per-agent API keys

- **Description:** Each agent has at least one API key. POST `/agents/<id>/keys` generates a new key (plain shown once, hash + last 4 stored).
- **Inputs:** Optional `label` (1..60 chars, default `default`).
- **Outputs:** An HTML partial showing the new key once.
- **Business Rules:**
  - Keys are 32 random bytes encoded base64url (≈43 chars).
  - Storage: `key_hash = sha256(plain_key).digest()` (raw bytes); `key_last4` = last 4 chars of the plain key (for fingerprint display).
  - The plain key is NEVER returned again after creation.
  - At least one non-revoked key MUST remain on each agent (revoking the last key is rejected with HTTP 409 and body `last_key_protected`). The revocation endpoint itself is not in this story; only the creation flow is in scope here.

### FR-023 (applied): Encrypt secrets at rest

- **Description:** `agents.provider_api_key_enc` is Fernet-encrypted using `LCNC_A2A_ENCRYPTION_KEY`.
- **Business Rules:**
  - HTTP responses MUST NEVER contain the plaintext provider API key after creation. The masked field convention `********` is used in HTML forms (used in US-003 edit form; in US-002 the create form takes plaintext input but renders nothing back).
  - The plain key string MUST NOT appear anywhere in the create response body or downstream HTML.

## Acceptance Tests

> Acceptance tests are mandatory: 100% must pass via `make test`. Loop fix → test → check until green.

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| Logged-in test client | Fixture `logged_in_client(email)` that performs `/login` and returns an `httpx.AsyncClient` with the session cookie set. | auto-generated | ready |
| Two-user setup | `alice@example.com` and `bob@example.com` users created via the login fixture. | auto-generated | ready |
| Sample ReAct prompt text | A short ReAct-style system prompt (≤ 1 KB) used in the create-agent happy path. | auto-generated (constant string in test module) | ready |
| Run-seeding helper | `seed_run(agent_id, started_at, status, tokens_in, tokens_out, duration_ms, loops, cost_usd)` test helper that inserts a row into `agent_runs`. | auto-generated | ready |
| 50-agent / 1000-run seed | A pytest fixture that bulk-inserts 50 agents and 1000 runs for the perf baseline (E2E-102). | auto-generated | ready |

### Happy Path Tests

#### E2E-006: Dashboard renders the user's agents only

- **Category:** happy
- **Scenario:** SC-002
- **Requirements:** FR-002
- **Preconditions:**
  - Two users exist: `alice@example.com`, `bob@example.com`.
  - Alice has 2 agents: `alice-agent-1`, `alice-agent-2`.
  - Bob has 1 agent: `bob-agent-1`.
  - Each of the 3 agents has 3 completed runs in the 30-day window.
- **Steps:**
  - Given the client is logged in as `alice@example.com`.
  - When the client GETs `/agents`.
  - Then response status is 200.
  - And the HTML body contains the substring `alice-agent-1` and `alice-agent-2`.
  - And the HTML body does NOT contain the substring `bob-agent-1`.
  - And for each Alice agent, a row of CSS class `agent-row` shows token totals, `requests=3`, average duration, average loops.
- **Cleanup:** Truncate `agents`, `agent_runs`, `agent_api_keys`, `users`, `sessions`.
- **Priority:** Critical

#### E2E-012: Create agent happy path (ReAct, defaults)

- **Category:** happy
- **Scenario:** SC-003
- **Requirements:** FR-003, FR-007, FR-023
- **Preconditions:**
  - Logged in as Alice.
  - `agents` table has no rows for Alice.
- **Steps:**
  - When the client POSTs `/agents` with form data:
    - `name = "fin-analyst"`
    - `description = "Public co analyst"`
    - `mode = "react"`
    - `model_provider = "openrouter"`
    - `model_endpoint = "https://openrouter.ai/api/v1"`
    - `model_id = "anthropic/claude-sonnet-4-5"`
    - `provider_api_key = "sk-or-v1-test"`
    - `system_prompt = <sample ReAct prompt>`
    - `max_loops = 10`, `max_tokens = 8000`, `similarity_threshold = 0.95`
  - Then the response status is 302 with `Location: /agents/<new_id>`.
  - And the follow-up GET `/agents/<new_id>` body contains the plain API key (43 chars base64url) exactly once and a copy button (HTML element with attribute `data-copy-target`).
  - And exactly one `agents` row exists with the supplied scalar fields, `status = "stopped"`, `created_at` within the last 5 seconds.
  - And `agents.provider_api_key_enc` is NOT equal to `b"sk-or-v1-test"` (BYTEA bytes differ from the literal) AND decrypts via the configured Fernet key to exactly the string `"sk-or-v1-test"`.
  - And exactly one `agent_api_keys` row exists with `label = "default"`, `key_hash = sha256(<plain key>).digest()`, and `key_last4` matching the last 4 chars of the plain key.
- **Cleanup:** Truncate `agents`, `agent_api_keys`, `users`, `sessions`.
- **Priority:** Critical

### Edge Case and Error Tests

#### E2E-007: Dashboard renders empty state when user has no agents

- **Category:** edge
- **Scenario:** SC-002
- **Requirements:** FR-002
- **Preconditions:**
  - A fresh user logged in with zero agents.
- **Steps:**
  - When the client GETs `/agents`.
  - Then response status is 200.
  - And the body contains the literal substring `Create agent`.
  - And the body contains an empty-state hint (substring `No agents yet` or equivalent — the literal text `Create agent` is the contract).
  - And the count of CSS selector `tr.agent-row` is 0.
- **Cleanup:** Truncate `users`, `sessions`.
- **Priority:** Medium

#### E2E-008: Dashboard aggregations only consider runs in the 30-day window

- **Category:** edge
- **Scenario:** SC-002
- **Requirements:** FR-002
- **Preconditions:**
  - User has 1 agent `agent-A` with 5 runs: 3 with `started_at = now()`, 2 with `started_at = now() - interval '60 days'`.
- **Steps:**
  - When the client GETs `/agents`.
  - Then for `agent-A`'s row, the rendered `requests` cell shows the literal text `3` (not `5`).
- **Cleanup:** Truncate `agents`, `agent_runs`, `users`, `sessions`.
- **Priority:** Medium

#### E2E-009: Counters survive a partial run failure

- **Category:** side effect / data integrity
- **Scenario:** SC-002
- **Requirements:** FR-002
- **Preconditions:**
  - One agent owned by Alice with two runs:
    - run 1: `status=completed`, `tokens_in=100`, `tokens_out=200`, `started_at=now()`.
    - run 2: `status=failed`, `tokens_in=50`, `tokens_out=0`, `started_at=now()`.
- **Steps:**
  - When the client GETs `/agents`.
  - Then the agent row shows `tokens_in=150`, `tokens_out=200`, `requests=2`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-010: Dashboard renders `n/a` for cost when any run lacks cost

- **Category:** edge
- **Scenario:** SC-002
- **Requirements:** FR-002, FR-022 (anticipated)
- **Preconditions:**
  - User has 1 agent with 2 runs in the window:
    - run 1: `cost_usd = 0.012`.
    - run 2: `cost_usd = NULL`.
- **Steps:**
  - When the client GETs `/agents`.
  - Then the cost column for that agent's row contains the literal `n/a`.
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-013: Create rejects missing name

- **Category:** failure
- **Scenario:** SC-003
- **Requirements:** FR-003
- **Steps:**
  - When the client POSTs `/agents` with `name` omitted (other valid fields present).
  - Then response status is 400 and the body contains the literal `name_required`.
  - And `SELECT count(*) FROM agents` returns 0.
- **Cleanup:** None.
- **Priority:** High

#### E2E-014: Create rejects duplicate name for same user

- **Category:** failure
- **Scenario:** SC-003
- **Requirements:** FR-003
- **Preconditions:**
  - Alice has an agent named `fin-analyst`.
- **Steps:**
  - When Alice POSTs `/agents` with `name = "fin-analyst"` and otherwise valid fields.
  - Then response status is 400 and the body contains the literal `name_taken`.
  - And `SELECT count(*) FROM agents WHERE user_id = <alice_id> AND name = 'fin-analyst'` returns 1 (no second row).
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-015: Create PE rejects missing planner / executor prompts

- **Category:** failure
- **Scenario:** SC-003
- **Requirements:** FR-003
- **Steps:**
  - When the client POSTs `/agents` with `mode = "plan_execute"` and `planner_prompt = ""`.
  - Then response status is 400 and the body contains the literal `prompts_required`.
  - And `SELECT count(*) FROM agents` returns 0.
- **Cleanup:** None.
- **Priority:** High

#### E2E-016: Create rejects `max_steps` outside `[1, 50]`

- **Category:** failure
- **Scenario:** SC-003
- **Requirements:** FR-003
- **Steps:**
  - When the client POSTs `/agents` with `mode = "plan_execute"`, valid prompts, `max_steps = 51`.
  - Then response status is 400 and the body contains the literal `max_steps_out_of_range`.
- **Cleanup:** None.
- **Priority:** High

#### E2E-017: API key persisted as hash + last4

- **Category:** side effect
- **Scenario:** SC-003
- **Requirements:** FR-007, FR-023
- **Preconditions:**
  - An agent created in the manner of E2E-012; capture the plain key `K` shown once.
- **Steps:**
  - When the test reads `agent_api_keys` directly via SQL.
  - Then `key_hash` equals the bytes `hashlib.sha256(K.encode()).digest()`.
  - And `key_last4` equals the last 4 characters of `K`.
  - And a SQL `SELECT * FROM agents` followed by `pg_dump` over the schema does NOT contain `K` anywhere as a substring (test concatenates all string columns and asserts `K not in` the concatenation).
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-018: Provider API key Fernet round trip

- **Category:** data integrity
- **Scenario:** SC-003
- **Requirements:** FR-023
- **Preconditions:**
  - An agent created with `provider_api_key = "sk-or-v1-roundtrip"`.
- **Steps:**
  - When the test reads `agents.provider_api_key_enc` and decrypts with the configured Fernet key.
  - Then the decrypted bytes decoded as UTF-8 equal the exact string `sk-or-v1-roundtrip`.
- **Cleanup:** Truncate.
- **Priority:** High

#### E2E-019: Create accepts Unicode in name and prompt

- **Category:** edge
- **Scenario:** SC-003
- **Requirements:** FR-003
- **Steps:**
  - When the client POSTs `/agents` with `name = "アナリスト 📊"` and `system_prompt = "あなたは…"` plus other valid fields.
  - Then response status is 302.
  - And the persisted `agents.name` equals exactly `"アナリスト 📊"` (byte-for-byte equal after UTF-8 round trip) and `agents.system_prompt` equals `"あなたは…"`.
- **Cleanup:** Truncate.
- **Priority:** Medium

#### E2E-097: Provider API key never returned in plain HTTP responses

- **Category:** security
- **Scenario:** (multi)
- **Requirements:** FR-023
- **Preconditions:**
  - An agent is created with `provider_api_key = "unique-secret-PROV-token-789"`.
- **Steps:**
  - When the client GETs `/agents/<id>` (the detail page that follows creation).
  - Then the response body does NOT contain the substring `unique-secret-PROV-token-789` anywhere (header values and body bytes are concatenated and checked).
  - And the response body contains `********` somewhere where the provider key would be displayed (the masked-field convention).
- **Cleanup:** Truncate.
- **Priority:** Critical

#### E2E-102: Dashboard performance baseline (50 agents, 1000 runs)

- **Category:** performance
- **Scenario:** (perf)
- **Requirements:** NFR 7.1
- **Preconditions:**
  - The 50-agent / 1000-run seed fixture is loaded for one user.
- **Steps:**
  - Given Alice is logged in.
  - When the client GETs `/agents` 21 times sequentially (the first call is the warm-up; subsequent 20 calls are measured).
  - Then the p95 latency over the 20 measured calls is strictly less than 500 ms.
- **Cleanup:** Truncate.
- **Priority:** Medium

## Constraints

### Files Not to Touch

- Files created by US-001 (auth, base templates, settings, encryption module) — extend via composition, do not edit semantics.

### Dependencies Not to Add

- No new runtime dependencies beyond what US-001 already declares.

### Patterns to Avoid

- Do NOT inline aggregation SQL in route handlers; place it in `services/agents.py`.
- Do NOT emit the plain provider API key in any flash message, log line, or trace span.
- Do NOT use Python `random` for API key generation; use `secrets.token_urlsafe(32)` (yields ~43 chars).

### Scope Boundary

- Editing an agent (FR-004), deleting (FR-005), starting/stopping (FR-006) are NOT in this story.
- Revoking API keys / DELETE `/agents/<id>/keys/<key_id>` is NOT in this story.
- The `agent_runs` table is created here in minimal form to support aggregation queries; the full schema (steps, plan, config_snapshot, etc.) lands in US-005.

## Non Regression

### Existing Tests That Must Pass

- All US-001 tests (login, session, encryption startup check, Carbon CSS, HTMX fragment).

### Behaviors That Must Not Change

- Login flow and session middleware remain unchanged.
- Carbon CSS link and theme tokens remain unchanged.

### API Contracts to Preserve

- `GET /login`, `POST /login` semantics from US-001.
- The `/agents` route (was a placeholder in US-001) is now fully implemented; the placeholder behavior is replaced, but the auth gate (302 to `/login` when anonymous) MUST continue to hold.
