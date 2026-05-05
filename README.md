# LCNC A2A Builder

A low-code/no-code builder for A2A (Agent-to-Agent protocol) agents. Build, configure, and host LLM-powered agents through a web UI; expose each one as a standards-compliant A2A endpoint that external clients can call.

## Status

This is **US-001 — Project foundation, dev mode login, base UI**. The application has:

- A runnable FastAPI server.
- Email-only dev mode login (no OAuth yet).
- A Carbon Design System styled empty dashboard.
- The cross-cutting plumbing (settings, encryption, sessions, CSRF, OpenTelemetry exporter, Jinja2 + HTMX) every later story builds on.

Subsequent user stories add the agent dashboard, A2A endpoints, executors, and runs history.

## Tech stack

- Python 3.13, FastAPI, uvicorn
- SQLAlchemy 2.x async + asyncpg + PostgreSQL 14+
- Alembic for migrations
- Jinja2 templates + HTMX
- IBM Carbon Design System CSS (reused from sibling `web-a2a`)
- pydantic-settings, itsdangerous, cryptography (Fernet)
- OpenTelemetry SDK with a JSONL exporter
- `uv` for dependency management

## Required environment variables

All env vars are prefixed `LCNC_A2A_`.

| Variable | Required | Purpose |
| --- | --- | --- |
| `LCNC_A2A_DATABASE_URL` | yes | `postgresql+asyncpg://user@host:5432/db` |
| `LCNC_A2A_ENCRYPTION_KEY` | dev: optional, prod: yes | Base64 Fernet key (`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`). If absent, a stable key is derived from the host machine-id (macOS / Linux) and a `WARNING` is logged. **Never** rely on this fallback in cloud / distributed deployments. |
| `LCNC_A2A_TRACE_FILE` | no | JSONL OpenTelemetry trace file (default `traces/lcnc-a2a.jsonl`) |

The `session_secret` used by `itsdangerous` to sign cookies and CSRF tokens is **bootstrapped from the database** on first boot (encrypted at rest with the encryption key) and never read from the environment. The encryption key fingerprint is also persisted: if it changes between runs, startup fails with an explicit error to avoid silent decryption failures of agent secrets.

## Quick start

```bash
# 1. Install deps
make sync

# 2. Provision the database (one-off, or use `make db-reset` to wipe)
createdb lcnc_a2a
make db-migrate

# 3. Export env (only DATABASE_URL is required in dev)
export LCNC_A2A_DATABASE_URL="postgresql+asyncpg://$(whoami)@localhost:5432/lcnc_a2a"
# Optional: provide an explicit encryption key (recommended for prod, optional in dev)
# export LCNC_A2A_ENCRYPTION_KEY="$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

# 4. Run
make run-frontend
# Open http://localhost:8001
```

## Development

```bash
make test            # run pytest
make lint            # ruff check
make format          # ruff format
make typecheck       # mypy
make check           # lint + format-check + typecheck + test
```

## Project layout

```
src/lcnc_a2a/
  main.py             # FastAPI app factory + entry point
  settings.py         # pydantic-settings
  db.py               # async engine + session factory
  crypto.py           # Fernet wrapper + startup check
  deps.py             # FastAPI dependencies
  auth/               # AuthProvider ABC, dev provider, sessions, CSRF
  models/             # SQLAlchemy 2.x ORM (User, Session)
  routes/             # /login, /logout, /agents
  observability/      # OpenTelemetry tracer + JSONL exporter
  themes/             # Carbon g100 / g10 token sets
  templates/          # Jinja2 templates (base, login, agents)
  static/css/         # carbon.css (verbatim copy from web-a2a)
alembic/              # migrations
tests/e2e/            # acceptance tests
```

## License

MIT
