# Testing notes

## Running

`make test` (preferred) or `uv run pytest`. There is no other sanctioned runner.

## PostgreSQL

The tests need a live PostgreSQL 14+ at `localhost:5432`. The default test DB name is `lcnc_a2a_test` (override with `LCNC_A2A_TEST_DB`) and the user defaults to the OS user (override with `LCNC_A2A_TEST_PG_USER`).

`tests/conftest.py`:
- Sets the `LCNC_A2A_*` env vars (auto-generates a Fernet key) before the app is imported.
- Creates the `pgcrypto` extension and runs `Base.metadata.create_all` once per session (synchronous engine via psycopg2).
- TRUNCATEs `sessions, users` before each test (function-scoped `db_engine` fixture using async engine).
- Provides `http_client` (httpx.AsyncClient with the ASGI transport, no follow_redirects) and `csrf_token` (parsed out of GET /login).
- `carbon_reference_bytes` returns the byte length of the locally copied carbon.css for the byte-budget assertion.

## Acceptance tests covered (US-001)

| Test ID | File | Notes |
|---|---|---|
| E2E-001 | tests/e2e/test_login.py | happy path |
| E2E-002 | tests/e2e/test_login.py | empty email -> 400 + `email_required` |
| E2E-003 | tests/e2e/test_login.py | email > 255 chars -> 400 + `email_too_long` |
| E2E-004 | tests/e2e/test_login.py | case-insensitive upsert |
| E2E-005 | tests/e2e/test_session.py | tampered cookie -> 302 /login |
| E2E-011 | tests/e2e/test_session.py | anonymous /agents -> 302 /login |
| E2E-099 | tests/e2e/test_session.py | duplicate of E2E-011 for traceability |
| E2E-101 | tests/e2e/test_session.py | HX-Request login returns a fragment with HX-Redirect |
| E2E-096 | tests/e2e/test_settings.py | missing `LCNC_A2A_ENCRYPTION_KEY` -> stderr message + non-zero exit (subprocess) |
| E2E-100 | tests/e2e/test_static_assets.py | /static/css/carbon.css served, byte length within 10% of reference |

## Adding tests

For US-002+: follow this conftest pattern. New ORM models will land in `src/lcnc_a2a/models/`; ensure they are imported via `lcnc_a2a.models.__init__` so `Base.metadata.create_all` picks them up.

## US-002 fixtures

- `login_as(email, name)` -> async client with the session cookie set.
- `fetch_user_id(email)` -> UUID lookup after login.
- `seed_user(email, name)` -> create users directly without the login flow.
- `seed_agent(user_id, name=..., ...)` -> insert an `agents` row via SQL.
- `seed_run(agent_id, started_at=..., status=..., tokens_in=..., ...)` -> insert an `agent_runs` row.
- `perf_seed(user_id)` -> bulk-insert 50 agents and 1000 runs for the E2E-102 perf baseline.

Per-test truncation cascades over `agent_runs, agent_api_keys, agents, sessions, users`.

## Acceptance tests covered (US-002)

| Test ID | File | Notes |
|---|---|---|
| E2E-006 | tests/e2e/test_dashboard.py | dashboard cross-user isolation |
| E2E-007 | tests/e2e/test_dashboard.py | empty state contains `Create agent` |
| E2E-008 | tests/e2e/test_dashboard.py | window filter excludes 60-day-old runs |
| E2E-009 | tests/e2e/test_dashboard.py | failed runs still counted |
| E2E-010 | tests/e2e/test_dashboard.py | NULL cost forces `n/a` cell |
| E2E-012 | tests/e2e/test_create_agent.py | create happy path + Fernet round-trip |
| E2E-013 | tests/e2e/test_create_agent.py | missing name → `name_required` |
| E2E-014 | tests/e2e/test_create_agent.py | duplicate name per user → `name_taken` |
| E2E-015 | tests/e2e/test_create_agent.py | PE missing prompts → `prompts_required` |
| E2E-016 | tests/e2e/test_create_agent.py | `max_steps=51` → `max_steps_out_of_range` |
| E2E-019 | tests/e2e/test_create_agent.py | Unicode persistence |
| E2E-017 | tests/e2e/test_api_keys.py | hash + last4 stored; plain key absent |
| E2E-018 | tests/e2e/test_api_keys.py | provider key Fernet round trip |
| E2E-097 | tests/e2e/test_api_keys.py | provider key never echoed; `********` shown |
| E2E-102 | tests/e2e/test_dashboard.py | p95 < 500 ms with 50 agents / 1000 runs |
