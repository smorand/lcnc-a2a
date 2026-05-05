# Secrets bootstrap

Where: `src/lcnc_a2a/services/app_secrets.py` + `src/lcnc_a2a/crypto_machine.py` + table `app_state` (migration `0005`).

## Goal

Reduce the env-var surface to a single mandatory variable (`LCNC_A2A_DATABASE_URL`) without sacrificing the security guarantees on production.

## What runs at startup

`create_app()` calls `bootstrap_secrets(database_url, env_encryption_key)` synchronously. The function:

1. **Resolves the Fernet key.**
   - If `LCNC_A2A_ENCRYPTION_KEY` is set, use it verbatim.
   - Else derive one via HKDF-SHA256(machine_id, salt=`b"lcnc-a2a-v1"`, info=`b"encryption-key"`, len=32) → urlsafe-b64 → Fernet key. Source of `machine_id`:
     - macOS: `IOPlatformUUID` from `ioreg -rd1 -c IOPlatformExpertDevice`.
     - Linux: `/etc/machine-id`, fallback `/var/lib/dbus/machine-id`.
     - Other platforms (Windows): `UnsupportedPlatformError`.
   - Logs a `WARNING` (`DERIVED_KEY_WARNING`) explaining this is a dev fallback only.

2. **Verifies the fingerprint.**
   - `fingerprint = sha256(key)[:16].hex()`
   - Reads `app_state` row `encryption_key_fingerprint`:
     - absent → INSERT (first boot).
     - matches → ok.
     - differs → log `FINGERPRINT_MISMATCH_ERROR` and raise `EncryptionKeyMismatchError`. **Startup fails** because every previously-stored secret (provider API keys, MCP secrets, `session_secret`) was encrypted with the old key and can no longer be decrypted.

3. **Resolves the session secret.**
   - Reads `app_state` row `session_secret` (Fernet-encrypted, base64 ASCII):
     - present → decrypt and use.
     - absent → generate `secrets.token_hex(32)`, encrypt, INSERT … ON CONFLICT DO NOTHING (race-safe between uvicorn workers), re-SELECT, decrypt, use.

The result (`AppSecrets`) is stored on `app.state.app_secrets`; `csrf` and `sessions` managers in `main.py` are constructed from `app_secrets.session_secret`.

## Storage

Single generic table `app_state(key TEXT PK, value TEXT, is_secret BOOLEAN, updated_at TIMESTAMPTZ)`.
Rows currently used:

| key | is_secret | content |
| --- | --- | --- |
| `encryption_key_fingerprint` | false | sha256(key)[:16] hex digest |
| `session_secret` | true | Fernet ciphertext of `secrets.token_hex(32)` |

The bootstrap uses a synchronous psycopg2 connection (the URL is auto-translated from `postgresql+asyncpg://` to `postgresql+psycopg2://`) so the whole flow completes before FastAPI begins serving and without an event loop.

## When to set `LCNC_A2A_ENCRYPTION_KEY` explicitly

Always:
- in CI / CD pipelines and Docker images,
- on cloud / managed runtimes (Cloud Run, Fly, Heroku, k8s),
- in any deployment with **multiple replicas** (each replica's machine-id differs),
- if the DB volume can be moved between machines.

Skipping the env var is safe only on a single dev workstation that is not redeployed.

## Operator-facing failure modes

- `Using machine-derived encryption key …` — informational; expected on first dev run, never expected in prod.
- `Encryption key fingerprint mismatch …` — startup aborts. Action: restore the original `LCNC_A2A_ENCRYPTION_KEY`, or accept data loss and run `make db-reset` to wipe.

## Tests

`tests/e2e/test_secrets_bootstrap.py` — env-key path, machine-derived path + WARNING, second-run reuse, fingerprint mismatch, env-precedence-over-machine.
`tests/e2e/test_settings.py` — E2E-096 (amended): missing `LCNC_A2A_ENCRYPTION_KEY` no longer blocks startup; the WARNING must be emitted.
