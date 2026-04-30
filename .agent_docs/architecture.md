# Architecture (US-001 foundation)

## Layers

```
HTTP (FastAPI)
   |
Routes (auth, dashboard)
   |
Dependencies (deps.py: DB session, CSRF, SessionManager, AuthProvider, Templates)
   |
Domain   AuthProvider (ABC)            CryptoService (Fernet)
   |          |                              ^
   |     DevModeAuthProvider                 |
   |          |                              |
SQLAlchemy 2.x async (asyncpg)        Settings (pydantic-settings)
   |
PostgreSQL 14+  (users, sessions)
```

## Startup sequence

1. `lcnc_a2a.main` is imported (e.g. by `uvicorn lcnc_a2a.main:app`).
2. `_load_settings()` checks `LCNC_A2A_ENCRYPTION_KEY` first; if missing, writes `LCNC_A2A_ENCRYPTION_KEY is required` to stderr and `sys.exit(1)`.
3. `Settings()` validates the rest (database_url, session_secret); on validation failure, exits with the same canonical message if encryption_key is the offender.
4. `CryptoService(settings.encryption_key)` validates the Fernet key; failure -> same canonical message + exit.
5. `Database`, `CSRFManager`, `SessionManager`, `DevModeAuthProvider`, `Jinja2Templates` get wired into `app.state`.
6. `configure_tracing(settings.trace_file)` registers the JSONL exporter (idempotent).
7. `/static`, `auth_routes`, `dashboard_routes` are mounted/included.
8. `lifespan` ensures `Database.close()` runs on shutdown.

## Auth flow (US-001)

- GET `/login` -> renders `login.html` with a fresh CSRF token (`URLSafeTimedSerializer`, salt `csrf`).
- POST `/login` -> validates CSRF, then validates inputs (`email_required`, `email_too_long`, `email_invalid`, `name_required`, `name_too_long`).
  - On success: `DevModeAuthProvider.authenticate` upserts the `users` row by lowercased email, `SessionManager.create` inserts a `sessions` row with `expires_at = now + 24h`, then 302 to `/agents` (or HX-Redirect for HTMX).
- GET `/agents` -> reads the `session` cookie, verifies the signature, looks up the row, hydrates the `User`. Anything missing -> 302 to `/login`.
- POST `/logout` -> deletes the row, clears the cookie.

## Key design notes

- `AuthProvider` is an ABC; future Google OAuth2 plugs in as another implementation. Routes never import the dev provider.
- Session cookies hold the *signed* session UUID; the row in `sessions` is the source of truth for expiry.
- CSRF and session signers share `LCNC_A2A_SESSION_SECRET` but use different `salt` values, so tokens cannot be cross-cast.
- The JSONL OTel exporter writes to `LCNC_A2A_TRACE_FILE` and redacts `api_key`, `authorization`, `password`, `token`, `cookie`, `set-cookie`, `llm.prompt`, `llm.response` (case-insensitive key match).
- `themes/` exposes `G100_TOKENS` (dark, default) and `G10_TOKENS` (light), both `ThemeTokens` instances; field names match the sibling `web-a2a` so styling code is portable.
