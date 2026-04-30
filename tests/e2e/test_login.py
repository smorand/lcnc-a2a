"""Login flow acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from lcnc_a2a.models.session import Session as SessionModel
from lcnc_a2a.models.user import User


async def _count(engine: AsyncEngine, table: str) -> int:
    factory = async_sessionmaker(engine)
    async with factory() as sess:
        result = await sess.execute(text(f"SELECT count(*) FROM {table}"))
        return int(result.scalar_one())


async def _users(engine: AsyncEngine) -> list[User]:
    factory = async_sessionmaker(engine)
    async with factory() as sess:
        result = await sess.execute(select(User))
        return list(result.scalars().all())


async def _sessions(engine: AsyncEngine) -> list[SessionModel]:
    factory = async_sessionmaker(engine)
    async with factory() as sess:
        result = await sess.execute(select(SessionModel))
        return list(result.scalars().all())


@pytest.mark.asyncio
async def test_e2e_001_login_happy_path(
    http_client: httpx.AsyncClient,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-001: dev mode login happy path."""
    response = await http_client.post(
        "/login",
        data={"email": "alice@example.com", "name": "Alice", "csrf_token": csrf_token},
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/agents"
    set_cookie = response.headers.get("set-cookie", "")
    assert "session=" in set_cookie

    users = await _users(db_engine)
    assert len(users) == 1
    user = users[0]
    assert user.email == "alice@example.com"
    assert user.name == "Alice"
    assert (datetime.now(UTC) - user.created_at).total_seconds() < 5

    sessions = await _sessions(db_engine)
    assert len(sessions) == 1
    sess = sessions[0]
    delta = sess.expires_at - datetime.now(UTC)
    assert timedelta(hours=23) <= delta <= timedelta(hours=25)


@pytest.mark.asyncio
async def test_e2e_002_login_rejects_empty_email(
    http_client: httpx.AsyncClient,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-002: empty email rejected with 400 + ``email_required``."""
    response = await http_client.post(
        "/login",
        data={"email": "", "name": "Alice", "csrf_token": csrf_token},
    )

    assert response.status_code == 400
    assert "email_required" in response.text
    assert await _count(db_engine, "users") == 0
    assert "session=" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_e2e_003_login_rejects_email_too_long(
    http_client: httpx.AsyncClient,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-003: email > 255 chars rejected with 400 + ``email_too_long``."""
    long_email = ("a" * 250) + "@x.com"
    assert len(long_email) == 256

    response = await http_client.post(
        "/login",
        data={"email": long_email, "name": "Alice", "csrf_token": csrf_token},
    )

    assert response.status_code == 400
    assert "email_too_long" in response.text
    assert await _count(db_engine, "users") == 0


@pytest.mark.asyncio
async def test_e2e_004_login_lowercases_email_idempotent(
    http_client: httpx.AsyncClient,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-004: case-insensitive lookup updates the existing row."""
    factory = async_sessionmaker(db_engine)
    async with factory() as sess:
        sess.add(User(email="bob@example.com", name="Bob"))
        await sess.commit()

    async with factory() as sess:
        existing = (await sess.execute(select(User))).scalar_one()
        prior_updated = existing.updated_at

    response = await http_client.post(
        "/login",
        data={"email": "Bob@Example.COM", "name": "Robert", "csrf_token": csrf_token},
    )

    assert response.status_code == 302

    async with factory() as sess:
        rows = (await sess.execute(text("SELECT count(*) FROM users WHERE email = 'bob@example.com'"))).scalar_one()
        assert rows == 1
        user = (await sess.execute(select(User))).scalar_one()
        assert user.name == "Robert"
        assert user.updated_at > prior_updated
