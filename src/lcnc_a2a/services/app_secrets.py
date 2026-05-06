"""Bootstrap encryption key + session secret at application startup.

The flow:

1. Resolve the Fernet key:
   - If ``LCNC_A2A_ENCRYPTION_KEY`` is provided, use it as-is.
   - Otherwise derive a stable key from the machine-id (macOS / Linux only)
     and emit a loud warning. Distributed / cloud deployments **must** set
     the env var explicitly.

2. Compare its fingerprint (``sha256(key)[:16].hex()``) to the value stored
   in ``app_state``:
   - Absent on first boot: insert it.
   - Mismatch: refuse to start with an explicit error - existing secrets
     in the database (provider API keys, MCP secrets, session_secret) were
     encrypted with a different key and would silently fail to decrypt.

3. Resolve the session secret:
   - If a row exists, decrypt it.
   - Otherwise generate ``secrets.token_hex(32)``, encrypt it, insert with
     ``ON CONFLICT DO NOTHING`` (race-safe between workers), and re-select.

The bootstrap is synchronous (psycopg2). It runs once during ``create_app``
before any request is served.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass

from sqlalchemy import Connection, create_engine, text

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.crypto_machine import derive_machine_fernet_key

logger = logging.getLogger(__name__)

_FINGERPRINT_KEY = "encryption_key_fingerprint"
_SESSION_SECRET_KEY = "session_secret"

DERIVED_KEY_WARNING = (
    "Using machine-derived encryption key (LCNC_A2A_ENCRYPTION_KEY not set). "
    "This is fine for local development but UNSAFE for production / "
    "distributed deployments: set LCNC_A2A_ENCRYPTION_KEY explicitly. "
    "Secrets will become unreadable if this machine changes."
)

FINGERPRINT_MISMATCH_ERROR = (
    "Encryption key fingerprint mismatch. Previously-stored secrets "
    "(agent provider keys, MCP secrets, session_secret) were encrypted "
    "with a different key and cannot be decrypted. Restore the original "
    "LCNC_A2A_ENCRYPTION_KEY or wipe the database (make db-reset) to start fresh."
)


class EncryptionKeyMismatchError(RuntimeError):
    """Raised when the stored fingerprint differs from the current key's fingerprint."""


@dataclass(frozen=True, slots=True)
class AppSecrets:
    """Result of the bootstrap: live crypto service + decoded session secret."""

    crypto: CryptoService
    session_secret: str
    derived_from_machine: bool


def _async_to_sync_url(database_url: str) -> str:
    """Translate the configured async URL to its sync counterpart.

    PostgreSQL: ``postgresql+asyncpg://`` → ``postgresql+psycopg2://``
    SQLite:     ``sqlite+aiosqlite://``   → ``sqlite://`` (stdlib driver)
    """
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if database_url.startswith("sqlite+aiosqlite://"):
        return database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return database_url


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _resolve_encryption_key(env_key: str | None) -> tuple[str, bool]:
    if env_key:
        return env_key, False
    logger.warning(DERIVED_KEY_WARNING)
    return derive_machine_fernet_key(), True


def bootstrap_secrets(database_url: str, env_encryption_key: str | None) -> AppSecrets:
    """Run the full bootstrap and return the materialised secrets."""
    key, derived = _resolve_encryption_key(env_encryption_key)
    crypto = CryptoService(key)
    fingerprint = _fingerprint(key)

    sync_url = _async_to_sync_url(database_url)
    engine = create_engine(sync_url, future=True)
    try:
        with engine.begin() as conn:
            stored_fingerprint = conn.execute(
                text("SELECT value FROM app_state WHERE key = :k"),
                {"k": _FINGERPRINT_KEY},
            ).scalar()

            if stored_fingerprint is None:
                conn.execute(
                    text(
                        "INSERT INTO app_state (key, value, is_secret) "
                        "VALUES (:k, :v, FALSE) ON CONFLICT (key) DO NOTHING"
                    ),
                    {"k": _FINGERPRINT_KEY, "v": fingerprint},
                )
            elif stored_fingerprint != fingerprint:
                logger.error(FINGERPRINT_MISMATCH_ERROR)
                raise EncryptionKeyMismatchError(FINGERPRINT_MISMATCH_ERROR)

            session_secret = _load_or_create_session_secret(conn, crypto)
    finally:
        engine.dispose()

    return AppSecrets(
        crypto=crypto,
        session_secret=session_secret,
        derived_from_machine=derived,
    )


def _load_or_create_session_secret(conn: Connection, crypto: CryptoService) -> str:
    encrypted = conn.execute(
        text("SELECT value FROM app_state WHERE key = :k"),
        {"k": _SESSION_SECRET_KEY},
    ).scalar()
    if encrypted is not None:
        return crypto.decrypt(encrypted.encode("ascii")).decode("utf-8")

    new_secret = secrets.token_hex(32)
    encrypted_value = crypto.encrypt(new_secret.encode("utf-8")).decode("ascii")
    conn.execute(
        text("INSERT INTO app_state (key, value, is_secret) VALUES (:k, :v, TRUE) ON CONFLICT (key) DO NOTHING"),
        {"k": _SESSION_SECRET_KEY, "v": encrypted_value},
    )
    re_read = conn.execute(
        text("SELECT value FROM app_state WHERE key = :k"),
        {"k": _SESSION_SECRET_KEY},
    ).scalar()
    if re_read is None:
        raise RuntimeError("session_secret row missing after insert; this should not happen")
    return crypto.decrypt(re_read.encode("ascii")).decode("utf-8")
