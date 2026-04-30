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
- `src/lcnc_a2a/settings.py` - pydantic-settings `Settings`
- `src/lcnc_a2a/db.py` - async engine and session factory
- `src/lcnc_a2a/crypto.py` - Fernet `CryptoService` + `LCNC_A2A_ENCRYPTION_KEY is required` startup error
- `src/lcnc_a2a/deps.py` - FastAPI dependencies (DB, CSRF, sessions, templates, ...)
- `src/lcnc_a2a/auth/` - `AuthProvider` ABC, `DevModeAuthProvider`, signed session cookies, CSRF tokens
- `src/lcnc_a2a/models/` - SQLAlchemy 2.x ORM (`User`, `Session`)
- `src/lcnc_a2a/routes/` - `/login`, `/logout`, `/agents` (placeholder until US-002)
- `src/lcnc_a2a/observability/` - OpenTelemetry tracer + JSONL exporter with redaction
- `src/lcnc_a2a/themes/` - Carbon `g100` (dark, default), `g10` (light), `ThemeTokens` dataclass
- `src/lcnc_a2a/templates/` - Jinja2 templates (`base.html`, `login.html`, `agents.html`, `partials/`)
- `src/lcnc_a2a/static/css/carbon.css` - copied verbatim from sibling `web-a2a`
- `alembic/versions/0001_initial.py` - users + sessions schema
- `tests/conftest.py` - test env, schema setup (sync), per-test TRUNCATE, ASGI httpx client
- `tests/e2e/` - acceptance tests for US-001 (E2E-001..005, 011, 096, 099, 100, 101)

## Conventions

- Routes in `routes/` use FastAPI `Depends` for everything (DB, providers). No global state.
- AuthProvider ABC must always be the abstraction; never import `DevModeAuthProvider` directly in route code.
- Encryption key never inlined; always loaded from `Settings.encryption_key`.
- CSRF/session signing uses `itsdangerous`; do NOT roll a custom signer.
- `make test` is the only sanctioned test runner.
- Templates support HTMX (`HX-Request: true`); state-mutating handlers must validate the CSRF token (HTTP 403 + body `csrf_invalid`).

## Quality Gate

`make check` must pass before each commit: ruff lint, ruff format-check, mypy strict, pytest (10/10 acceptance tests).

## Documentation Index

- `.agent_docs/architecture.md` - high-level component architecture
- `.agent_docs/testing.md` - PostgreSQL test setup and conventions
