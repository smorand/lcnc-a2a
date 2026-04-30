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
