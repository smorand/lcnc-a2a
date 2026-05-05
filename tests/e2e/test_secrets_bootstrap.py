"""Bootstrap-secrets behaviour: encryption key resolution, fingerprint check,
session_secret get-or-create."""

from __future__ import annotations

import logging

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lcnc_a2a.services.app_secrets import (
    EncryptionKeyMismatchError,
    bootstrap_secrets,
)


def _async_url(engine: AsyncEngine) -> str:
    return str(engine.url.render_as_string(hide_password=False))


async def test_env_provided_key_persists_fingerprint_and_secret(db_engine: AsyncEngine) -> None:
    key = Fernet.generate_key().decode()

    secrets = bootstrap_secrets(database_url=_async_url(db_engine), env_encryption_key=key)

    assert secrets.derived_from_machine is False
    assert secrets.session_secret

    async with db_engine.begin() as conn:
        rows = (await conn.execute(text("SELECT key, is_secret FROM app_state ORDER BY key"))).all()
    assert [(r[0], r[1]) for r in rows] == [
        ("encryption_key_fingerprint", False),
        ("session_secret", True),
    ]


async def test_second_bootstrap_reuses_session_secret(db_engine: AsyncEngine) -> None:
    key = Fernet.generate_key().decode()

    first = bootstrap_secrets(database_url=_async_url(db_engine), env_encryption_key=key)
    second = bootstrap_secrets(database_url=_async_url(db_engine), env_encryption_key=key)

    assert first.session_secret == second.session_secret


async def test_fingerprint_mismatch_raises(db_engine: AsyncEngine) -> None:
    key_a = Fernet.generate_key().decode()
    key_b = Fernet.generate_key().decode()
    assert key_a != key_b

    bootstrap_secrets(database_url=_async_url(db_engine), env_encryption_key=key_a)

    with pytest.raises(EncryptionKeyMismatchError):
        bootstrap_secrets(database_url=_async_url(db_engine), env_encryption_key=key_b)


async def test_machine_derived_path_emits_warning(
    db_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="lcnc_a2a.services.app_secrets"):
        secrets = bootstrap_secrets(database_url=_async_url(db_engine), env_encryption_key=None)

    assert secrets.derived_from_machine is True
    messages = [r.message for r in caplog.records]
    assert any("machine-derived encryption key" in m.lower() for m in messages), messages


async def test_env_key_takes_precedence_over_machine_id(db_engine: AsyncEngine) -> None:
    key = Fernet.generate_key().decode()

    secrets = bootstrap_secrets(database_url=_async_url(db_engine), env_encryption_key=key)

    assert secrets.derived_from_machine is False
