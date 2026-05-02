"""Shared fixtures for the LCNC A2A test suite."""

from __future__ import annotations

import getpass
import os
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
        await conn.execute(
            text(
                "TRUNCATE TABLE agent_messages, agent_contexts, agent_run_steps, "
                "agent_runs, agent_mcp_servers, agent_api_keys, agents, sessions, "
                "users RESTART IDENTITY CASCADE"
            )
        )
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


LoginFixture = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def login_as(
    http_client: httpx.AsyncClient,
    csrf_token: str,
) -> LoginFixture:
    """Return an async login helper that posts /login and keeps the session cookie."""

    async def _login(email: str, name: str = "Test User") -> httpx.AsyncClient:
        response = await http_client.post(
            "/login",
            data={"email": email, "name": name, "csrf_token": csrf_token},
        )
        assert response.status_code in (200, 302), response.text
        return http_client

    return _login


FetchUserIdFn = Callable[[str], Awaitable[uuid.UUID]]


@pytest.fixture
async def fetch_user_id(db_engine: AsyncEngine) -> FetchUserIdFn:
    """Look up the users.id for an email (assumes it exists)."""

    async def _fetch(email: str) -> uuid.UUID:
        async with db_engine.begin() as conn:
            row = await conn.execute(text("SELECT id FROM users WHERE email = :e"), {"e": email.lower()})
            return uuid.UUID(str(row.scalar_one()))

    return _fetch


SeedAgentFn = Callable[..., Awaitable[uuid.UUID]]


@pytest.fixture
async def seed_agent(db_engine: AsyncEngine) -> SeedAgentFn:
    """Insert an agent row via SQL and return its id."""

    async def _seed(
        user_id: uuid.UUID,
        *,
        name: str,
        mode: str = "react",
        model_provider: str = "openrouter",
        model_endpoint: str = "https://openrouter.ai/api/v1",
        model_id: str = "anthropic/claude-sonnet-4-5",
        provider_api_key_enc: bytes = b"x" * 32,
        max_loops: int = 10,
        max_tokens: int = 8000,
        similarity_threshold: float | None = 0.95,
        max_steps: int | None = None,
        system_prompt: str | None = "system",
        status: str = "stopped",
    ) -> uuid.UUID:
        agent_id = uuid.uuid4()
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agents (id, user_id, name, mode, model_provider, "
                    "model_endpoint, model_id, provider_api_key_enc, max_loops, "
                    "max_tokens, similarity_threshold, max_steps, system_prompt, "
                    "status) VALUES (:id, :user_id, :name, :mode, :model_provider, "
                    ":model_endpoint, :model_id, :provider_api_key_enc, :max_loops, "
                    ":max_tokens, :similarity_threshold, :max_steps, :system_prompt, "
                    ":status)"
                ),
                {
                    "id": agent_id,
                    "user_id": user_id,
                    "name": name,
                    "mode": mode,
                    "model_provider": model_provider,
                    "model_endpoint": model_endpoint,
                    "model_id": model_id,
                    "provider_api_key_enc": provider_api_key_enc,
                    "max_loops": max_loops,
                    "max_tokens": max_tokens,
                    "similarity_threshold": similarity_threshold,
                    "max_steps": max_steps,
                    "system_prompt": system_prompt,
                    "status": status,
                },
            )
        return agent_id

    return _seed


SeedUserFn = Callable[..., Awaitable[uuid.UUID]]


@pytest.fixture
async def seed_user(db_engine: AsyncEngine) -> SeedUserFn:
    """Insert a users row via SQL and return its id."""

    async def _seed(email: str, name: str = "Test User") -> uuid.UUID:
        user_id = uuid.uuid4()
        async with db_engine.begin() as conn:
            await conn.execute(
                text("INSERT INTO users (id, email, name) VALUES (:id, :email, :name)"),
                {"id": user_id, "email": email.lower(), "name": name},
            )
        return user_id

    return _seed


SeedRunFn = Callable[..., Awaitable[uuid.UUID]]


@pytest.fixture
async def seed_run(db_engine: AsyncEngine) -> SeedRunFn:
    """Insert an ``agent_runs`` row directly via SQL. Returns its id."""

    async def _seed(
        agent_id: uuid.UUID,
        *,
        started_at: datetime | None = None,
        status: str = "completed",
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        duration_ms: int | None = None,
        loops: int | None = None,
        cost_usd: Decimal | float | None = None,
    ) -> uuid.UUID:
        run_id = uuid.uuid4()
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agent_runs (id, agent_id, status, started_at, "
                    "duration_ms, loops, tokens_in, tokens_out, cost_usd) "
                    "VALUES (:id, :agent_id, :status, :started_at, "
                    ":duration_ms, :loops, :tokens_in, :tokens_out, :cost_usd)"
                ),
                {
                    "id": run_id,
                    "agent_id": agent_id,
                    "status": status,
                    "started_at": started_at or datetime.now(UTC),
                    "duration_ms": duration_ms,
                    "loops": loops,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cost_usd": cost_usd,
                },
            )
        return run_id

    return _seed


@pytest.fixture
def perf_seed_window_days() -> int:
    """Window size for the perf-baseline seed (kept aligned with default)."""
    return 30


@pytest.fixture
async def perf_seed(
    db_engine: AsyncEngine,
    perf_seed_window_days: int,
) -> Callable[[uuid.UUID], Awaitable[None]]:
    """Bulk-insert 50 agents and 1000 runs for the perf baseline (E2E-102)."""

    async def _seed(user_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        agent_ids = [uuid.uuid4() for _ in range(50)]
        agent_rows = [
            {
                "id": agent_id,
                "user_id": user_id,
                "name": f"perf-agent-{i:03d}",
                "mode": "react",
                "model_provider": "openrouter",
                "model_endpoint": "https://openrouter.ai/api/v1",
                "model_id": "anthropic/claude-sonnet-4-5",
                "provider_api_key_enc": b"x" * 32,
                "max_loops": 10,
                "max_tokens": 8000,
                "status": "stopped",
            }
            for i, agent_id in enumerate(agent_ids)
        ]
        run_rows = []
        for i in range(1000):
            run_rows.append(
                {
                    "id": uuid.uuid4(),
                    "agent_id": agent_ids[i % 50],
                    "status": "completed",
                    "started_at": now - timedelta(minutes=i),
                    "duration_ms": 100 + i,
                    "loops": 1 + (i % 5),
                    "tokens_in": 50 + i,
                    "tokens_out": 100 + i,
                    "cost_usd": Decimal("0.001"),
                }
            )

        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agents (id, user_id, name, mode, model_provider, "
                    "model_endpoint, model_id, provider_api_key_enc, max_loops, "
                    "max_tokens, status) VALUES (:id, :user_id, :name, :mode, "
                    ":model_provider, :model_endpoint, :model_id, "
                    ":provider_api_key_enc, :max_loops, :max_tokens, :status)"
                ),
                agent_rows,
            )
            await conn.execute(
                text(
                    "INSERT INTO agent_runs (id, agent_id, status, started_at, "
                    "duration_ms, loops, tokens_in, tokens_out, cost_usd) VALUES "
                    "(:id, :agent_id, :status, :started_at, :duration_ms, :loops, "
                    ":tokens_in, :tokens_out, :cost_usd)"
                ),
                run_rows,
            )

    return _seed
