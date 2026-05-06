"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


class Database:
    """Owns the async engine and session factory.

    Supports both PostgreSQL (production) and SQLite (local self-host) via
    standard SQLAlchemy URLs:
        postgresql+asyncpg://user@host/dbname
        sqlite+aiosqlite:///./lcnc-a2a.db
    """

    __slots__ = ("_engine", "_session_factory")

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, future=True, echo=False)
        # SQLite needs an explicit PRAGMA on every connection to honour
        # ``ON DELETE CASCADE`` foreign keys; PG enforces them by default.
        if self._engine.dialect.name == "sqlite":

            @event.listens_for(self._engine.sync_engine, "connect")
            def _enable_sqlite_fks(dbapi_connection: Any, _record: Any) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def session(self) -> AsyncIterator[AsyncSession]:
        """FastAPI dependency yielding an AsyncSession."""
        async with self._session_factory() as sess:
            yield sess

    async def close(self) -> None:
        await self._engine.dispose()
