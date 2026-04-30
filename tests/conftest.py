"""Shared fixtures for the LCNC A2A test suite."""

from __future__ import annotations

import getpass
import os
import re
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DB_NAME = os.environ.get("LCNC_A2A_TEST_DB", "lcnc_a2a_test")
PG_USER = os.environ.get("LCNC_A2A_TEST_PG_USER") or getpass.getuser()
ASYNC_URL = f"postgresql+asyncpg://{PG_USER}@localhost:5432/{TEST_DB_NAME}"
SYNC_URL = f"postgresql+psycopg2://{PG_USER}@localhost:5432/{TEST_DB_NAME}"

CARBON_REFERENCE = REPO_ROOT / "src" / "lcnc_a2a" / "static" / "css" / "carbon.css"


def _generate_fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture(scope="session", autouse=True)
def _set_test_env() -> Iterator[None]:
    """Populate the env that ``Settings()`` and ``main`` need."""
    os.environ["LCNC_A2A_DATABASE_URL"] = ASYNC_URL
    os.environ.setdefault("LCNC_A2A_ENCRYPTION_KEY", _generate_fernet_key())
    os.environ.setdefault("LCNC_A2A_SESSION_SECRET", "test-session-secret")
    os.environ.setdefault("LCNC_A2A_TRACE_FILE", str(REPO_ROOT / "traces" / "test.jsonl"))
    yield


@pytest.fixture(scope="session", autouse=True)
def _create_schema(_set_test_env: None) -> Iterator[None]:
    """Create tables once per session using a synchronous engine."""
    from lcnc_a2a.models import Base

    sync_engine = create_engine(SYNC_URL, future=True)
    with sync_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        Base.metadata.drop_all(bind=conn)
        Base.metadata.create_all(bind=conn)
    sync_engine.dispose()
    yield


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """Yield an async engine and truncate tables before the test."""
    engine = create_async_engine(ASYNC_URL, future=True)
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE sessions, users RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
def app(db_engine: AsyncEngine) -> FastAPI:
    """Build a fresh FastAPI app for each test."""
    import importlib

    import lcnc_a2a.main as main_module

    importlib.reload(main_module)
    return main_module.app


@pytest.fixture
async def http_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """ASGI httpx client bound to the app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        yield client


@pytest.fixture
async def csrf_token(http_client: httpx.AsyncClient) -> str:
    """Get a CSRF token by GET /login and parsing the form."""
    response = await http_client.get("/login")
    assert response.status_code == 200, response.text
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None, f"CSRF token not found in:\n{response.text[:500]}"
    return match.group(1)


@pytest.fixture
def carbon_reference_bytes() -> int:
    """Byte length of the reference carbon.css copy."""
    return CARBON_REFERENCE.stat().st_size
