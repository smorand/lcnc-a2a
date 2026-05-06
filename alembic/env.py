"""Alembic migration environment for LCNC A2A."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from lcnc_a2a.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolved_url() -> str:
    """Pick URL from env var if set, otherwise from alembic.ini.

    Alembic runs synchronously, so async drivers must be swapped to their
    sync counterparts:
      ``postgresql+asyncpg://`` → ``postgresql+psycopg2://``
      ``sqlite+aiosqlite://``   → ``sqlite://`` (stdlib sqlite3 driver)
    """
    raw = os.environ.get("LCNC_A2A_DATABASE_URL") or config.get_main_option("sqlalchemy.url") or ""
    return raw.replace("postgresql+asyncpg://", "postgresql+psycopg2://").replace("sqlite+aiosqlite://", "sqlite://")


def run_migrations_offline() -> None:
    """Run migrations in offline mode (no DB connection)."""
    context.configure(
        url=_resolved_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB."""
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _resolved_url()
    connectable = engine_from_config(cfg, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
