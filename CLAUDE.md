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
- `src/lcnc_a2a/services/` - CRUD + aggregations (`agents.py` with `update_agent`/`delete_agent_cascade`/`set_status`, `api_keys.py`, `mcp_discovery.py` with create/update/delete/discover for MCP servers, `runs_view.py` with US-008 read-only helpers + 4 KB truncation)
- `src/lcnc_a2a/mcp_client/` - MCP SDK wrappers (`stdio.py` with env scrubbing + 10s timeout + PID tracking, `streamable_http.py`, `errors.py`)
- `src/lcnc_a2a/schemas/` - form validation (`agent_form.py` with contract error codes; `require_provider_api_key=False` for edit)
- `src/lcnc_a2a/routes/` - `auth.py` (login/logout), `dashboard.py` (`/agents` listing), `agents.py` (UI flows + dispatch to A2A on `Authorization` / `application/json`), `mcp.py` (MCP CRUD + discover), `a2a.py` (GET `.well-known/agent-card.json` + `handle_a2a_post` SSE dispatcher), `runs.py` (GET `/agents/<id>/runs` list, expand partial, full-payload endpoint)
- `src/lcnc_a2a/a2a/` - `envelope.py` (SendStreamingMessage / TaskStatusUpdate / TaskArtifactUpdate), `card.py` (Agent Card builder), `sse.py` (SSE encoder)
- `src/lcnc_a2a/auth/api_key.py` - constant-time bearer key validation (`hmac.compare_digest`)
- `src/lcnc_a2a/llm/` - `provider.py` (LlmProvider ABC, OpenRouterProvider, OpenAiCompatibleProvider via httpx), `tool_format.py` (MCP → OpenAI tools), `embeddings.py` (FR-019 retry, `resolve_embedding_model`)
- `src/lcnc_a2a/executors/` - `base.py` (ExecutorContext + `invoke_mcp_tool` + `collect_tools`), `dispatcher.py`, `simple.py` (Simple-mode loop, retries, OTel), `react.py` (ReAct loop, similarity stop, guardrails), `plan_execute.py` (planner + stage-parallel executor + replan + synthesis), `synthesis.py` (force-synthesis helper)
- `src/lcnc_a2a/services/similarity.py` - pure-Python `cosine_similarity()`
- `src/lcnc_a2a/services/plan_validator.py` - PE planner JSON validator (FR-016, FR-020)
- `src/lcnc_a2a/services/plan_substitution.py` - PE `${step_N.output}` resolver
- `src/lcnc_a2a/mcp_client/tool_caller.py` - call_tool_stdio / call_tool_http (env scrubbing reused)
- `src/lcnc_a2a/services/cancellation.py` - in-process `run_id → asyncio.Event` registry
- `src/lcnc_a2a/services/messages.py` - context get/create + soft 50 / hard 1000 cap, OpenAI payload builder
- `src/lcnc_a2a/services/runs.py` - AgentRun lifecycle (create / append step / finalize / list_running_run_ids)
- `src/lcnc_a2a/observability/` - OpenTelemetry tracer + JSONL exporter with redaction
- `src/lcnc_a2a/themes/` - Carbon `g100` (dark, default), `g10` (light), `ThemeTokens` dataclass
- `src/lcnc_a2a/templates/` - Jinja2 templates (`base.html`, `login.html`, `agents/list.html`, `agents/new.html`, `agents/detail.html`, `agents/runs_list.html`, `agents/partials/`)
- `src/lcnc_a2a/static/css/carbon.css` - copied verbatim from sibling `web-a2a`
- `alembic/versions/0001_initial.py` - users + sessions schema
- `alembic/versions/0002_agents_keys_runs.py` - agents, agent_api_keys, agent_runs schema
- `alembic/versions/0003_lifecycle_cascade_tables.py` - agent_mcp_servers, agent_contexts, agent_messages, agent_run_steps (final schema; populated in US-004/US-005)
- `alembic/versions/0004_agent_runs_full_schema.py` - extends agent_runs with context_id, a2a_task_id, stop_reason, completed_at, plan, final_answer, config_snapshot
- `tests/conftest.py` - test env, schema setup (sync), per-test TRUNCATE, ASGI httpx client, `login_as`, `seed_*` helpers
- `tests/e2e/fixtures/` - fake MCP stdio servers (`fake_mcp_stdio.py`, `fake_mcp_hang.py`, `fake_mcp_fail.py`)
- `tests/e2e/_mcp_http_helpers.py` - respx helpers for streamable-HTTP MCP mocking
- `tests/e2e/_a2a_helpers.py` - StubLlm, seed_started_agent, post_a2a, fetch_run/messages/steps helpers
- `tests/e2e/_react_helpers.py` - StubEmbedding, make_embedding, add_react_tool_call, add_final_answer, seed_started_react_agent
- `tests/e2e/_pe_helpers.py` - PE plan/step/synthesis stub builders, `seed_started_pe_agent`, `seed_pe_mcp`
- `tests/e2e/fixtures/fake_mcp_add.py` - stdio MCP fixture exposing `add`, `flaky`, `noop` tools
- `tests/e2e/fixtures/fake_mcp_pe.py` - stdio MCP fixture exposing `search`, `get_market_data`, `compute_ratios`, `echo`, `slow` tools (US-007)
- `tests/e2e/` - acceptance tests for US-001..US-008 (E2E-001..085, 086..102, plus E2E-043..047 for US-008)

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

`make check` must pass before each commit: ruff lint, ruff format-check, mypy strict, pytest (105/105 acceptance tests).

## Documentation Index

- `.agent_docs/architecture.md` - high-level component architecture
- `.agent_docs/testing.md` - PostgreSQL test setup and conventions
- `.agent_docs/agents_dashboard.md` - US-002 routes, aggregation rules, API-key flow
- `.agent_docs/agent_lifecycle.md` - US-003 edit/delete/start/stop, cascade tables
- `.agent_docs/mcp_tools.md` - US-004 MCP discovery (stdio + streamable_http), env scrubbing, transport-change rules
- `.agent_docs/a2a_executor.md` - US-005 A2A surface, Simple executor, cancellation, OTel redaction
- `.agent_docs/react_executor.md` - US-006 ReAct loop, similarity stop, guardrails, embedding retry
- `.agent_docs/plan_execute_executor.md` - US-007 planner + stage-parallel executor + replan + synthesis
- `.agent_docs/runs_history_ui.md` - US-008 runs history page, expand partial, 4 KB truncation rule
