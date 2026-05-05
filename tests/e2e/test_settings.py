"""Settings / startup acceptance tests."""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from lcnc_a2a.services.app_secrets import bootstrap_secrets


async def test_e2e_096_missing_encryption_key_uses_machine_fallback(
    db_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E2E-096 (amended 2026-05-03): without LCNC_A2A_ENCRYPTION_KEY the app starts and logs a warning.

    Original spec contract: 'startup blocked'. Amended to support a
    machine-id-derived fallback for dev convenience. The warning must always be
    emitted so operators do not deploy in this state by accident.
    """
    async_url = str(db_engine.url.render_as_string(hide_password=False))

    with caplog.at_level(logging.WARNING, logger="lcnc_a2a.services.app_secrets"):
        secrets = bootstrap_secrets(database_url=async_url, env_encryption_key=None)

    assert secrets.derived_from_machine is True
    assert secrets.session_secret  # generated and decoded
    assert any("machine-derived encryption key" in r.message.lower() for r in caplog.records)
