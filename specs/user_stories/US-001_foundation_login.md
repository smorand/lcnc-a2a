# US-001: Project foundation, dev mode login, base UI

> Parent Spec: specs/2026-04-30_20:06:59-lcnc-a2a-builder.md
> Status: ready
> Priority: 1
> Depends On: none
> Complexity: M

## Objective

Stand up the LCNC A2A Builder web application skeleton: a runnable FastAPI app, a PostgreSQL schema managed via Alembic, the Carbon Design System theming reused from `web-a2a`, the dev mode email login flow, and the cross-cutting plumbing (settings, encryption utility, session middleware, CSRF, OpenTelemetry exporter scaffolding) that every later story depends on. After this story, an unauthenticated user can open the app, sign in by email, and land on an empty dashboard rendered with Carbon CSS.

## Technical Context

### Stack

- Python 3.13.
- FastAPI (HTTP server), uvicorn (ASGI runner).
- SQLAlchemy 2.x async ORM with `Mapped[]` annotations, `asyncpg` driver, PostgreSQL 14+.
- Alembic for migrations (`make db-migrate`).
- Jinja2 templates, HTMX for partial swaps.
- `pydantic-settings` for environment configuration (env prefix `LCNC_A2A_`).
- `itsdangerous` for signed session cookies and CSRF tokens.
- `cryptography.fernet` for secret-at-rest encryption.
- `uv` for dependency management.
- IBM Carbon Design System CSS reused from sibling project `web-a2a` (do NOT re-style; copy `carbon.css` verbatim and the theme tokens).

### Relevant File Structure

```
lcnc-a2a/
├── Makefile                              # sync, db-migrate, run-frontend, test, lint
├── pyproject.toml
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_initial.py               # creates users, sessions tables
├── src/
│   └── lcnc_a2a/
│       ├── __init__.py
│       ├── main.py                       # FastAPI app factory
│       ├── settings.py                   # pydantic-settings Settings()
│       ├── db.py                         # async engine, sessionmaker
│       ├── crypto.py                     # Fernet wrapper, startup key check
│       ├── auth/
│       │   ├── __init__.py
│       │   ├── provider.py               # AuthProvider ABC
│       │   ├── dev_provider.py           # email-only dev provider
│       │   ├── session.py                # itsdangerous signer + middleware
│       │   └── csrf.py
│       ├── observability/
│       │   ├── __init__.py
│       │   ├── otel.py                   # tracer setup
│       │   └── jsonl_exporter.py         # JSONL span exporter + redaction
│       ├── models/
│       │   ├── __init__.py
│       │   ├── base.py                   # DeclarativeBase, UUID PK mixin
│       │   ├── user.py                   # User model
│       │   └── session.py                # Session model
│       ├── routes/
│       │   ├── __init__.py
│       │   ├── auth.py                   # /login GET + POST, /logout
│       │   └── dashboard.py              # /agents stub (empty placeholder until US-002)
│       ├── templates/
│       │   ├── base.html                 # layout: head, Carbon CSS link, nav
│       │   ├── login.html
│       │   └── partials/
│       └── static/
│           ├── css/carbon.css            # copied from web-a2a verbatim
│           └── themes/                   # ThemeTokens dataclass + CSS variables
│               ├── __init__.py
│               ├── tokens.py
│               └── g100.py / g10.py
└── tests/
    ├── conftest.py                       # postgres test fixture, app fixture, http client
    └── e2e/
        ├── test_login.py
        ├── test_session.py
        ├── test_static_assets.py
        └── test_settings.py
```

### Existing Patterns

This is a greenfield project; reference patterns from the sibling `web-a2a` project. Specifically:

- Copy `web-a2a/src/agent_chat/static/css/carbon.css` verbatim into `src/lcnc_a2a/static/css/carbon.css`.
- Copy the theme abstraction (`web-a2a/src/agent_chat/themes/`) and rename the namespace to `lcnc_a2a`. Default theme is `g100` (dark); light theme is `g10`. Token names from the `ThemeTokens` dataclass MUST be preserved.
- Use `web-a2a`'s base template structure (head, body, nav) and adapt nav links to LCNC routes.
- Use the same async SQLAlchemy initialization pattern (one engine, one async session factory exposed via FastAPI dependency).

### Data Model (excerpt)

#### users

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | server-side default `gen_random_uuid()` |
| email | VARCHAR(255) UNIQUE | stored lowercased |
| name | VARCHAR(255) | |
| created_at | TIMESTAMPTZ | server default `now()` |
| updated_at | TIMESTAMPTZ | server default `now()`, ON UPDATE |

#### sessions

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | session cookie value |
| user_id | UUID FK → users.id ON DELETE CASCADE | |
| expires_at | TIMESTAMPTZ | now() + 24h |
| created_at | TIMESTAMPTZ | |

### Settings (Pydantic)

Required env vars (all prefixed `LCNC_A2A_`):

| Var | Required | Notes |
|---|---|---|
| `LCNC_A2A_DATABASE_URL` | yes | `postgresql+asyncpg://...` |
| `LCNC_A2A_ENCRYPTION_KEY` | yes | base64 Fernet key, 32 bytes |
| `LCNC_A2A_SESSION_SECRET` | yes | itsdangerous signing key |
| `LCNC_A2A_TRACE_FILE` | no | default `traces/lcnc-a2a.jsonl` |

Missing `LCNC_A2A_ENCRYPTION_KEY` MUST cause the process to exit non-zero with stderr containing the literal `LCNC_A2A_ENCRYPTION_KEY is required`.

## Functional Requirements

### FR-001: Dev mode email login

- **Description:** App provides a `/login` page accepting email and display name. On submit, the app upserts a `users` row keyed by lowercased email, issues a signed session cookie, and redirects to `/agents`.
- **Inputs:** `email` (string, 1..255 chars, valid RFC 5321 format), `name` (string, 1..255 chars).
- **Outputs:** HTTP 302 to `/agents`, `Set-Cookie: session=<signed>` header.
- **Business Rules:**
  - Email comparison is case-insensitive on the lowercased form.
  - Existing `users` row for the same lowercased email is UPDATED (`name`, `updated_at`); no duplicate row created.
  - Session expires after 24 hours.
  - The `AuthProvider` ABC is defined in code (`auth/provider.py`) with one concrete implementation `DevModeAuthProvider`. Future Google OAuth2 plugs in by adding another implementation; route handlers MUST not couple to the dev provider directly.
  - Cookie value is signed via `itsdangerous.URLSafeSerializer` with `LCNC_A2A_SESSION_SECRET`. Tampering MUST invalidate the session.

### FR-023 (foundation slice): Encryption utility & startup check

- **Description:** A `crypto.py` module exposes `encrypt(b: bytes) -> bytes` and `decrypt(b: bytes) -> bytes` backed by Fernet.
- **Business Rules:**
  - The Fernet key is loaded from `LCNC_A2A_ENCRYPTION_KEY` once at app startup.
  - If the env var is missing or malformed, the app MUST refuse to start (process exits non-zero) with stderr containing the literal `LCNC_A2A_ENCRYPTION_KEY is required`.
  - The encryption module is imported by later stories; in US-001 only the startup check is exercised.

### FR-025: Carbon Design System UI

- **Description:** Reuse the `web-a2a` `carbon.css` and theme tokens.
- **Business Rules:**
  - File at `src/lcnc_a2a/static/css/carbon.css` is BYTE-IDENTICAL to the upstream copy in `web-a2a/src/agent_chat/static/css/carbon.css` (allowed deviation: ±10% in length to permit a future slim subset).
  - Default theme is `g100` (dark), light theme is `g10`.
  - Pages MUST link `<link rel="stylesheet" href="/static/css/carbon.css">` in the head of `base.html`.
  - The `ThemeTokens` dataclass field names from `web-a2a` MUST be preserved.

### FR-026 (foundation slice): SSR with Jinja2 + HTMX & CSRF

- **Description:** Full HTML page renders on initial GET; HTMX-driven endpoints return HTML fragments when `HX-Request: true` is present.
- **Business Rules:**
  - All state-mutating forms include a CSRF token (signed via `itsdangerous`).
  - POST handlers reject requests without a valid CSRF token (HTTP 403, body `csrf_invalid`). The login form MUST embed and validate this token.
  - When an HTMX request hits a save endpoint, response body MUST be a fragment (no `<html>`, no `<head>`).

## Acceptance Tests

> **Acceptance tests are mandatory: 100% must pass.** A user story is NOT considered implemented until **every single acceptance test below passes**. The implementing agent MUST loop (fix code → run tests → check results → repeat) until all acceptance tests pass with zero failures. Tests MUST be validated through `make test` (no other invocation method allowed).

### Test Data

| Data | Description | Source | Status |
|------|-------------|--------|--------|
| Test PostgreSQL DB | Per-worker schema spun up by the conftest fixture; truncated between tests. | auto-generated | ready |
| Fernet key | A valid base64 Fernet key set in `LCNC_A2A_ENCRYPTION_KEY` for the test app fixture. | auto-generated | ready |
| Session secret | A random hex string set in `LCNC_A2A_SESSION_SECRET` for the test app fixture. | auto-generated | ready |
| Reference `carbon.css` | Path to `web-a2a/src/agent_chat/static/css/carbon.css` for byte-length comparison. | user-provided (sibling repo path) | pending |

### Happy Path Tests

#### E2E-001: Dev mode login happy path

- **Category:** happy
- **Scenario:** SC-001
- **Requirements:** FR-001
- **Preconditions:**
  - PostgreSQL test DB clean.
  - App running with the test fixture (env vars set).
- **Steps:**
  - Given no `users` row exists for `alice@example.com`.
  - When the client POSTs `/login` with form data `{email: "alice@example.com", name: "Alice"}` (with a valid CSRF token).
  - Then the response status is 302 with `Location: /agents` and a `Set-Cookie: session=<base64 signed value>` header is present.
  - And a `users` row exists with `email = "alice@example.com"` (lowercased), `name = "Alice"`, `created_at` within last 5 seconds.
  - And a `sessions` row exists for that user with `expires_at` between `now()+23h` and `now()+25h`.
- **Cleanup:** Truncate `users`, `sessions`.
- **Priority:** Critical

#### E2E-100: Carbon CSS is served and pages reference it

- **Category:** happy
- **Scenario:** (theming)
- **Requirements:** FR-025, FR-026
- **Preconditions:**
  - User logged in.
  - The reference `carbon.css` byte length is known to the test (computed from the sibling `web-a2a` repo path or a fixture constant).
- **Steps:**
  - Given a logged in user.
  - When the client GETs `/agents`.
  - Then the response HTML body contains `<link rel="stylesheet" href="/static/css/carbon.css">`.
  - And a subsequent GET `/static/css/carbon.css` returns HTTP 200 with `Content-Type: text/css` and a body length within 10% of the reference byte length.
- **Cleanup:** None.
- **Priority:** Medium

### Edge Case and Error Tests

> Edge case and error tests are equally mandatory. Each test specifies the exact expected error (HTTP status, error code, error message).

#### E2E-002: Login rejects empty email

- **Category:** failure
- **Scenario:** SC-001
- **Requirements:** FR-001
- **Preconditions:**
  - App running.
- **Steps:**
  - Given the user is not logged in.
  - When the client POSTs `/login` with `{email: "", name: "Alice"}` and a valid CSRF token.
  - Then response status is 400 and the HTML body contains the literal string `email_required`.
  - And no `users` row was created (`SELECT count(*) FROM users` returns 0).
  - And no `Set-Cookie: session=` header was issued.
- **Cleanup:** None (DB already empty).
- **Priority:** High

#### E2E-003: Login rejects email > 255 chars

- **Category:** failure
- **Scenario:** SC-001
- **Requirements:** FR-001
- **Preconditions:**
  - App running.
- **Steps:**
  - Given the user is not logged in.
  - When the client POSTs `/login` with `{email: "a"*250 + "@x.com", name: "Alice"}` (length 256) and a valid CSRF token.
  - Then response status is 400 and the body contains the literal `email_too_long`.
  - And no `users` row was created.
- **Cleanup:** None.
- **Priority:** High

#### E2E-004: Login lowercases email on lookup (idempotent upsert)

- **Category:** edge
- **Scenario:** SC-001
- **Requirements:** FR-001
- **Preconditions:**
  - A `users` row exists for `bob@example.com` with `name = "Bob"`.
- **Steps:**
  - Given that row.
  - When the client POSTs `/login` with `{email: "Bob@Example.COM", name: "Robert"}` and a valid CSRF token.
  - Then response status is 302.
  - And `SELECT count(*) FROM users WHERE email = 'bob@example.com'` returns 1 (no duplicate row created).
  - And `name` is now `Robert` and `updated_at` strictly advanced compared to the prior value.
- **Cleanup:** Truncate `users`, `sessions`.
- **Priority:** Medium

#### E2E-005: Tampered session cookie redirects to login

- **Category:** failure
- **Scenario:** SC-001
- **Requirements:** FR-001
- **Preconditions:**
  - A successful login was performed and a session cookie obtained.
- **Steps:**
  - Given the cookie value `session=<signed_value>`.
  - When the client flips the last character of `<signed_value>` and GETs `/agents` with the tampered cookie.
  - Then response status is 302 with `Location: /login` (signature verification fails, session invalidated).
  - And no `sessions` row was created or modified.
- **Cleanup:** Truncate `users`, `sessions`.
- **Priority:** High

#### E2E-011: Anonymous request to `/agents` redirects to `/login`

- **Category:** security
- **Scenario:** SC-002
- **Requirements:** FR-002 (via auth gate from FR-001)
- **Preconditions:**
  - No session cookie present.
- **Steps:**
  - When the client GETs `/agents` with no cookies.
  - Then response status is 302 with `Location: /login`.
- **Cleanup:** None.
- **Priority:** Critical

#### E2E-099: GET /agents requires session (duplicate of E2E-011 for traceability)

- **Category:** security
- **Scenario:** (multi)
- **Requirements:** FR-002
- **Preconditions:**
  - No session cookie present.
- **Steps:**
  - When the client GETs `/agents`.
  - Then response status is 302 with `Location: /login`.
- **Cleanup:** None.
- **Priority:** Critical

#### E2E-096: Encryption key missing at startup blocks the app

- **Category:** security
- **Scenario:** (multi)
- **Requirements:** FR-023
- **Preconditions:**
  - The `LCNC_A2A_ENCRYPTION_KEY` env var is unset.
- **Steps:**
  - When the test launches the app process (subprocess) with all other env vars valid but `LCNC_A2A_ENCRYPTION_KEY` unset.
  - Then the process exits with a non-zero return code within 5 seconds.
  - And the captured stderr contains the literal string `LCNC_A2A_ENCRYPTION_KEY is required`.
- **Cleanup:** None.
- **Priority:** Critical

#### E2E-101: HTMX partial responses (placeholder endpoint)

- **Category:** edge
- **Scenario:** (theming)
- **Requirements:** FR-026
- **Preconditions:**
  - A logged in user.
  - A demo HTMX endpoint exists in this story (e.g., `POST /login` reuses this; or a placeholder `/agents` partial). The login form will be re-tested in later stories with HX-Request as well; in US-001, the test targets the login flow.
- **Steps:**
  - Given the login page.
  - When the client POSTs `/login` with `HX-Request: true` and valid form fields.
  - Then the response is an HTML fragment: the body MUST NOT contain `<html` or `<head` (case-insensitive substring check).
  - And the response includes either an `HX-Redirect: /agents` header OR a 200 status with a fragment that triggers the redirect.
- **Cleanup:** Truncate `users`, `sessions`.
- **Priority:** Medium

## Constraints

### Files Not to Touch

- This story creates the project from scratch; nothing exists to leave alone. Future stories must NOT modify the foundational files in this story without explicit need.

### Dependencies Not to Add

- Allowed runtime dependencies for THIS story: `fastapi`, `uvicorn[standard]`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `jinja2`, `pydantic`, `pydantic-settings`, `itsdangerous`, `cryptography`, `httpx` (for OTel HTTP and tests), `python-multipart` (for form data), `opentelemetry-sdk`.
- Allowed test dependencies: `pytest`, `pytest-asyncio`, `pytest-postgresql`, `respx`.
- Do NOT add: any UI framework beyond Carbon CSS, any Tailwind / Bootstrap, any client-side state library, any auth lib (build the dev provider directly).

### Patterns to Avoid

- Do NOT couple the route handlers to the concrete `DevModeAuthProvider`. Always go through the `AuthProvider` ABC.
- Do NOT inline the Fernet key string anywhere in source. It MUST come from settings.
- Do NOT roll a custom CSRF library; use `itsdangerous`.

### Scope Boundary

- The `/agents` route in this story is a placeholder that requires login and renders an empty layout (a Carbon-styled page with the empty state hint to be properly implemented in US-002). It does NOT list agents.
- API key generation (FR-007) is NOT in this story.
- OpenTelemetry exporter wiring is set up but no spans beyond app boot are tested here; deeper LLM redaction tests live with the executor stories.

## Non Regression

### Existing Tests That Must Pass

- None (greenfield project).

### Behaviors That Must Not Change

- N/A.

### API Contracts to Preserve

- N/A.
