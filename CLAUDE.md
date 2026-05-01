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
- `src/lcnc_a2a/models/` - SQLAlchemy 2.x ORM (`User`, `Session`, `Agent`, `AgentApiKey`, `AgentRun`)
- `src/lcnc_a2a/services/` - CRUD + aggregations (`agents.py`, `api_keys.py`)
- `src/lcnc_a2a/schemas/` - form validation (`agent_form.py` with contract error codes)
- `src/lcnc_a2a/routes/` - `auth.py` (login/logout), `dashboard.py` (`/agents` listing), `agents.py` (`/agents/new`, POST `/agents`, `/agents/{id}`, `/agents/{id}/keys`)
- `src/lcnc_a2a/observability/` - OpenTelemetry tracer + JSONL exporter with redaction
- `src/lcnc_a2a/themes/` - Carbon `g100` (dark, default), `g10` (light), `ThemeTokens` dataclass
- `src/lcnc_a2a/templates/` - Jinja2 templates (`base.html`, `login.html`, `agents/list.html`, `agents/new.html`, `agents/detail.html`, `agents/partials/`)
- `src/lcnc_a2a/static/css/carbon.css` - copied verbatim from sibling `web-a2a`
- `alembic/versions/0001_initial.py` - users + sessions schema
- `alembic/versions/0002_agents_keys_runs.py` - agents, agent_api_keys, agent_runs schema
- `tests/conftest.py` - test env, schema setup (sync), per-test TRUNCATE, ASGI httpx client, `login_as`, `seed_*` helpers
- `tests/e2e/` - acceptance tests for US-001 + US-002 (E2E-001..019, 096..102)

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

`make check` must pass before each commit: ruff lint, ruff format-check, mypy strict, pytest (25/25 acceptance tests).

## Documentation Index

- `.agent_docs/architecture.md` - high-level component architecture
- `.agent_docs/testing.md` - PostgreSQL test setup and conventions
- `.agent_docs/agents_dashboard.md` - US-002 routes, aggregation rules, API-key flow
