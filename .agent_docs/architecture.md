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
2. `Settings()` loads env (only `LCNC_A2A_DATABASE_URL` is mandatory; `encryption_key` is optional).
3. `bootstrap_secrets(database_url, env_encryption_key)` runs synchronously (psycopg2):
   - resolves the Fernet key (env value, or HKDF-derived from machine-id with a `WARNING`);
   - verifies / inserts the `encryption_key_fingerprint` row in `app_state` (mismatch -> `EncryptionKeyMismatchError`, startup aborts);
   - get-or-creates an encrypted `session_secret` row.
4. `Database`, `CSRFManager` and `SessionManager` (built from `app_secrets.session_secret`), `DevModeAuthProvider`, `Jinja2Templates` get wired into `app.state`.
5. `configure_tracing(settings.trace_file)` registers the JSONL exporter (idempotent).
6. `/static`, `auth_routes`, `dashboard_routes` are mounted/included.
7. `lifespan` ensures `Database.close()` runs on shutdown.

See `.agent_docs/secrets_bootstrap.md` for the full bootstrap contract.

## Auth flow (US-001)

- GET `/login` -> renders `login.html` with a fresh CSRF token (`URLSafeTimedSerializer`, salt `csrf`).
- POST `/login` -> validates CSRF, then validates inputs (`email_required`, `email_too_long`, `email_invalid`, `name_required`, `name_too_long`).
  - On success: `DevModeAuthProvider.authenticate` upserts the `users` row by lowercased email, `SessionManager.create` inserts a `sessions` row with `expires_at = now + 24h`, then 302 to `/agents` (or HX-Redirect for HTMX).
- GET `/agents` -> reads the `session` cookie, verifies the signature, looks up the row, hydrates the `User`. Anything missing -> 302 to `/login`.
- POST `/logout` -> deletes the row, clears the cookie.

## Key design notes

- `AuthProvider` is an ABC; future Google OAuth2 plugs in as another implementation. Routes never import the dev provider.
- Session cookies hold the *signed* session UUID; the row in `sessions` is the source of truth for expiry.
- CSRF and session signers share the bootstrapped `session_secret` (DB-stored, encrypted) but use different `salt` values, so tokens cannot be cross-cast.
- The JSONL OTel exporter writes to `LCNC_A2A_TRACE_FILE` and redacts `api_key`, `authorization`, `password`, `token`, `cookie`, `set-cookie`, `llm.prompt`, `llm.response` (case-insensitive key match).
- `themes/` exposes `G100_TOKENS` (dark, default) and `G10_TOKENS` (light), both `ThemeTokens` instances; field names match the sibling `web-a2a` so styling code is portable.
