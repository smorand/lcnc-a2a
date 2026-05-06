"""Smoke tests for the SQLite backend.

Validates that the schema migrations apply on a fresh SQLite database, the
secrets bootstrap works without psycopg2, and basic ORM CRUD round-trips.
The PostgreSQL E2E suite remains the canonical compatibility surface; this
file covers the local self-host path.
"""

from __future__ import annotations

import os
import secrets
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lcnc_a2a.models.agent import Agent
from lcnc_a2a.services.app_secrets import bootstrap_secrets


def _alembic_config_for(db_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    return cfg


@pytest.fixture
def sqlite_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """Return ``(sync_url, async_url)`` for a fresh, empty SQLite file.

    Also redirects ``LCNC_A2A_DATABASE_URL`` so Alembic's ``env.py`` picks
    the SQLite URL up (it prefers the env var over the config setting).
    """
    db_file = tmp_path / "lcnc-a2a.db"
    sync_url = f"sqlite:///{db_file}"
    async_url = f"sqlite+aiosqlite:///{db_file}"
    monkeypatch.setenv("LCNC_A2A_DATABASE_URL", sync_url)
    return sync_url, async_url


def test_alembic_migrations_apply_on_sqlite(sqlite_paths: tuple[str, str]) -> None:
    """Every migration upgrades cleanly against an empty SQLite file."""
    sync_url, _ = sqlite_paths
    cfg = _alembic_config_for(sync_url)
    command.upgrade(cfg, "head")

    # Spot-check the resulting schema.
    from sqlalchemy import create_engine, inspect

    engine = create_engine(sync_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "users",
        "sessions",
        "agents",
        "agent_api_keys",
        "agent_runs",
        "agent_run_steps",
        "agent_mcp_servers",
        "agent_contexts",
        "agent_messages",
        "app_state",
    }
    assert expected.issubset(tables), tables - expected
    engine.dispose()


def test_bootstrap_secrets_runs_on_sqlite(sqlite_paths: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """``bootstrap_secrets`` should work on SQLite (no psycopg2 path needed)."""
    sync_url, _ = sqlite_paths
    command.upgrade(_alembic_config_for(sync_url), "head")

    key = Fernet.generate_key().decode()
    secrets1 = bootstrap_secrets(database_url=sync_url, env_encryption_key=key)
    secrets2 = bootstrap_secrets(database_url=sync_url, env_encryption_key=key)

    # Same key → same fingerprint → same session_secret resolved both times.
    assert secrets1.session_secret == secrets2.session_secret
    assert secrets1.derived_from_machine is False


@pytest.mark.asyncio
async def test_orm_round_trip_on_sqlite(sqlite_paths: tuple[str, str]) -> None:
    """Basic CRUD against the SQLite backend exercises the variant types."""
    sync_url, async_url = sqlite_paths
    command.upgrade(_alembic_config_for(sync_url), "head")

    engine = create_async_engine(async_url, future=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    user_id = uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, email, name, created_at, updated_at) "
                "VALUES (:id, :email, :name, current_timestamp, current_timestamp)"
            ),
            {"id": str(user_id), "email": f"{secrets.token_hex(4)}@x.test", "name": "Tester"},
        )
        agent = Agent(
            user_id=user_id,
            name="local-agent",
            description="sqlite smoke",
            mode="simple",
            model_provider="openai_compatible",
            model_endpoint="http://localhost:9121/v1",
            model_id="local-model",
            provider_api_key_enc=None,
            max_loops=10,
            max_tokens=8000,
            status="started",
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)

    # PK was filled by the Python-level ``default=uuid.uuid4`` (no PG
    # ``gen_random_uuid()`` available on SQLite).
    assert isinstance(agent.id, uuid.UUID)

    async with sessionmaker() as session:
        loaded = await session.get(Agent, agent.id)
        assert loaded is not None
        assert loaded.name == "local-agent"
        assert loaded.mode == "simple"

    await engine.dispose()


def test_sqlite_url_is_routed_to_sync_driver_in_bootstrap(
    sqlite_paths: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The async SQLite URL must be translated to the sync stdlib driver
    so ``bootstrap_secrets`` (which uses sync SQLAlchemy) can connect."""
    _, async_url = sqlite_paths

    # Ensure migrations are applied before bootstrap (it doesn't run them).
    sync_url, _ = sqlite_paths
    command.upgrade(_alembic_config_for(sync_url), "head")

    # Set the env var so the derived-key warning is suppressed.
    monkeypatch.setenv("LCNC_A2A_ENCRYPTION_KEY", Fernet.generate_key().decode())
    bootstrap_secrets(
        database_url=async_url,  # ``sqlite+aiosqlite://...``
        env_encryption_key=os.environ["LCNC_A2A_ENCRYPTION_KEY"],
    )
