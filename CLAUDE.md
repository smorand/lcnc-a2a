# LCNC A2A Builder

## Overview

A web app to build, configure and host A2A agents through a low-code/no-code UI. Each agent gets an A2A endpoint (Agent Card + JSON-RPC) that external clients can call.

Tech: Python 3.13, FastAPI, async SQLAlchemy + asyncpg + PostgreSQL 14+, Alembic, Jinja2 + HTMX, IBM Carbon Design CSS, OpenTelemetry.

## Key Commands

```
make sync          # Install deps with uv
make db-migrate    # alembic upgrade head
make db-reset      # Drop+recreate dev DB and re-migrate (DEV ONLY)
make run-frontend  # uvicorn on http://localhost:8000
make test          # pytest (US-001 acceptance tests)
make lint / make format / make typecheck
make check         # lint + format-check + typecheck + test
```

Required env (prefix `LCNC_A2A_`): `DATABASE_URL`, `ENCRYPTION_KEY`, `SESSION_SECRET`. Optional: `TRACE_FILE` (defaults to `traces/lcnc-a2a.jsonl`).

## Project Structure

- `src/lcnc_a2a/main.py` - FastAPI app factory, startup encryption-key check, lifespan, console entry point
- `src/lcnc_a2a/settings.py` - pydantic-settings `Settings` (incl. `metrics_window_days`)
- `src/lcnc_a2a/db.py` - async engine and session factory
- `src/lcnc_a2a/crypto.py` - Fernet `CryptoService` + `LCNC_A2A_ENCRYPTION_KEY is required` startup error
- `src/lcnc_a2a/deps.py` - FastAPI dependencies (DB, CSRF, sessions, templates, settings, crypto, ...)
- `src/lcnc_a2a/auth/` - `AuthProvider` ABC, `DevModeAuthProvider`, signed session cookies, CSRF tokens, `fetch_current_user`
- `src/lcnc_a2a/models/` - SQLAlchemy 2.x ORM (`User`, `Session`, `Agent`, `AgentApiKey`, `AgentRun`, `AgentRunStep`, `AgentMcpServer`, `AgentContext`, `AgentMessage`)
- `src/lcnc_a2a/services/` - CRUD + aggregations (`agents.py` with `update_agent`/`delete_agent_cascade`/`set_status`, `api_keys.py`, `mcp_discovery.py` with create/update/delete/discover for MCP servers)
- `src/lcnc_a2a/mcp_client/` - MCP SDK wrappers (`stdio.py` with env scrubbing + 10s timeout + PID tracking, `streamable_http.py`, `errors.py`)
- `src/lcnc_a2a/schemas/` - form validation (`agent_form.py` with contract error codes; `require_provider_api_key=False` for edit)
- `src/lcnc_a2a/routes/` - `auth.py` (login/logout), `dashboard.py` (`/agents` listing), `agents.py` (`/agents/new`, POST `/agents`, `/agents/{id}`, `/agents/{id}/edit`, POST `/agents/{id}` (update or `_method=DELETE`), `/agents/{id}/start`, `/agents/{id}/stop`, `/agents/{id}/keys`), `mcp.py` (`/agents/{id}/mcp`, `/agents/{id}/mcp/new`, POST `/agents/{id}/mcp`, GET/POST `/agents/{id}/mcp/{server_id}` with `_method=DELETE`, POST `/agents/{id}/mcp/{server_id}/discover`)
- `src/lcnc_a2a/observability/` - OpenTelemetry tracer + JSONL exporter with redaction
- `src/lcnc_a2a/themes/` - Carbon `g100` (dark, default), `g10` (light), `ThemeTokens` dataclass
- `src/lcnc_a2a/templates/` - Jinja2 templates (`base.html`, `login.html`, `agents/list.html`, `agents/new.html`, `agents/detail.html`, `agents/partials/`)
- `src/lcnc_a2a/static/css/carbon.css` - copied verbatim from sibling `web-a2a`
- `alembic/versions/0001_initial.py` - users + sessions schema
- `alembic/versions/0002_agents_keys_runs.py` - agents, agent_api_keys, agent_runs schema
- `alembic/versions/0003_lifecycle_cascade_tables.py` - agent_mcp_servers, agent_contexts, agent_messages, agent_run_steps (final schema; populated in US-004/US-005)
- `tests/conftest.py` - test env, schema setup (sync), per-test TRUNCATE, ASGI httpx client, `login_as`, `seed_*` helpers
- `tests/e2e/fixtures/` - fake MCP stdio servers (`fake_mcp_stdio.py`, `fake_mcp_hang.py`, `fake_mcp_fail.py`)
- `tests/e2e/_mcp_http_helpers.py` - respx helpers for streamable-HTTP MCP mocking
- `tests/e2e/` - acceptance tests for US-001..US-004 (E2E-001..041, 096..102)

## Conventions

- Routes in `routes/` use FastAPI `Depends` for everything (DB, providers). No global state.
- AuthProvider ABC must always be the abstraction; never import `DevModeAuthProvider` directly in route code.
- Encryption key never inlined; always loaded from `Settings.encryption_key`.
- CSRF/session signing uses `itsdangerous`; do NOT roll a custom signer.
- `make test` is the only sanctioned test runner.
- Templates support HTMX (`HX-Request: true`); state-mutating handlers must validate the CSRF token (HTTP 403 + body `csrf_invalid`).
- Aggregation queries live in `services/`, never inlined in route handlers.
- API keys use `secrets.token_urlsafe(32)`; never `random`. Plain key shown once via short-lived httponly cookie consumed on the next detail render.
- Form validation errors raise `AgentFormError` with a contract code (e.g., `name_required`); the route renders the form with HTTP 400 and the literal code in the body.

## Quality Gate

`make check` must pass before each commit: ruff lint, ruff format-check, mypy strict, pytest (45/45 acceptance tests).

## Documentation Index

- `.agent_docs/architecture.md` - high-level component architecture
- `.agent_docs/testing.md` - PostgreSQL test setup and conventions
- `.agent_docs/agents_dashboard.md` - US-002 routes, aggregation rules, API-key flow
- `.agent_docs/agent_lifecycle.md` - US-003 edit/delete/start/stop, cascade tables
- `.agent_docs/mcp_tools.md` - US-004 MCP discovery (stdio + streamable_http), env scrubbing, transport-change rules
