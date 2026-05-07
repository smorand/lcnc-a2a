"""End-to-end tests for orphan-run handling.

A run can be left in the ``running`` state if the client disconnects
mid-stream or the process crashes. The executors finalize via the outer
``finally`` to avoid that, and ``reap_abandoned_runs`` mops up anything
left over from a previous process.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lcnc_a2a.services import runs as runs_service
from tests.e2e._a2a_helpers import (
    StubLlm,
    install_llm_mock,
    seed_started_agent,
)


@pytest.mark.asyncio
async def test_executor_cancellation_finalizes_run_as_cancelled(
    seed_user,
    db_engine: AsyncEngine,
    respx_mock,
) -> None:
    """Cancelling the executor's task mid-stream finalizes via the outer ``finally``.

    Equivalent to a client read-timeout / disconnect: the SSE consumer task
    is killed while ``provider.chat()`` is still awaiting, and the executor's
    last-resort ``finally`` should close the run as
    ``cancelled / client_disconnected`` rather than leaving it ``running``.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from lcnc_a2a.a2a.sse import A2AEventEmitter
    from lcnc_a2a.crypto import CryptoService
    from lcnc_a2a.executors.base import ExecutorContext
    from lcnc_a2a.executors.simple import SimpleExecutor
    from lcnc_a2a.llm.provider import OpenRouterProvider
    from lcnc_a2a.models.agent import Agent
    from lcnc_a2a.models.agent_context import AgentContext

    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, _plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")

    release = asyncio.Event()
    stalled = asyncio.Event()

    async def on_call(_index: int, _payload: object) -> None:
        stalled.set()
        await release.wait()

    stub = StubLlm(on_call=on_call)
    stub.add_text("late answer")
    install_llm_mock(respx_mock, stub)

    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    crypto = CryptoService(__import__("os").environ["LCNC_A2A_ENCRYPTION_KEY"])

    async def _drive_executor() -> None:
        async with sessionmaker() as session:
            agent = await session.get(Agent, agent_id)
            assert agent is not None
            ctx_row = AgentContext(agent_id=agent_id)
            session.add(ctx_row)
            await session.flush()
            from lcnc_a2a.services import runs as _runs

            run_row = await _runs.create_run(
                session,
                agent=agent,
                context_id=ctx_row.id,
                a2a_task_id=str(uuid.uuid4()),
            )
            await session.commit()

            ctx = ExecutorContext(
                agent=agent,
                run=run_row,
                context_id=ctx_row.id,
                user_text="hi",
                mcp_servers=[],
                provider_api_key="sk-fake",
                cancellation=asyncio.Event(),
                emitter=A2AEventEmitter(task_id=run_row.a2a_task_id or "", context_id=str(ctx_row.id)),
            )
            executor = SimpleExecutor(db=session, provider=OpenRouterProvider(), crypto=crypto)
            async for _chunk in executor.run(ctx):
                pass

    driver_task = asyncio.create_task(_drive_executor())
    await asyncio.wait_for(stalled.wait(), timeout=5.0)
    driver_task.cancel()
    release.set()
    with pytest.raises((asyncio.CancelledError, BaseException)):
        await driver_task
    # Give the shielded orphan-finalize a beat to commit.
    await asyncio.sleep(0.3)

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT status, stop_reason FROM agent_runs WHERE agent_id = :a"),
                {"a": agent_id},
            )
        ).one()
    assert row.status == "cancelled", row
    assert row.stop_reason == "client_disconnected", row


@pytest.mark.asyncio
async def test_schedule_orphan_finalize_uses_a_separate_session(
    seed_user,
    db_engine: AsyncEngine,
) -> None:
    """Regression test for asyncpg ``another operation in progress`` race.

    The orphan finalize must NOT use the request-scoped session passed in:
    that session is being torn down by FastAPI's dependency cleanup at the
    same moment, and asyncpg refuses concurrent operations on a connection.
    The fix spawns a detached task with a fresh session built off the same
    engine. This test asserts:

      1. After ``schedule_orphan_finalize`` returns, the spawned task is
         tracked so we can await it.
      2. The original session can be closed immediately (mimicking the
         FastAPI cleanup) without the orphan task observing any error.
      3. The run row is still written to its terminal state by the
         independent session.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, _plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")

    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    run_id = uuid.uuid4()

    async with sessionmaker() as setup:
        await setup.execute(
            text(
                "INSERT INTO agent_runs (id, agent_id, a2a_task_id, status, started_at) "
                "VALUES (:id, :a, :t, 'running', :ts)"
            ),
            {"id": run_id, "a": agent_id, "t": str(uuid.uuid4()), "ts": datetime.now(UTC)},
        )
        await setup.commit()

    # Open the "request session", schedule the finalize, then immediately
    # close the request session — exactly the race condition the production
    # bug exhibited.
    request_session = sessionmaker()
    await request_session.__aenter__()
    runs_service.schedule_orphan_finalize(
        request_session,
        run_id=run_id,
        cancel_event_set=False,
        exc_type=asyncio.CancelledError,
        tokens_in=0,
        tokens_out=0,
        cost_usd=None,
        loops=0,
    )
    # Spawned task should be tracked.
    assert any(t.get_name() == f"orphan-finalize-{run_id}" for t in runs_service._ORPHAN_TASKS)
    await request_session.__aexit__(None, None, None)

    # Wait for the detached finalize task. It must complete without raising.
    pending = [t for t in runs_service._ORPHAN_TASKS if t.get_name() == f"orphan-finalize-{run_id}"]
    for t in pending:
        await t  # propagates any exception the task swallowed; we only swallow at db level

    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text("SELECT status, stop_reason FROM agent_runs WHERE id = :id"),
                {"id": run_id},
            )
        ).one()
    assert row.status == "cancelled", row
    assert row.stop_reason == "client_disconnected", row


@pytest.mark.asyncio
async def test_reap_abandoned_runs_finalizes_old_running_rows(
    seed_user,
    db_engine: AsyncEngine,
) -> None:
    """The startup reaper closes ``running`` runs older than the threshold."""
    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, _ = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")

    # Insert a synthetic running run with a started_at well in the past so it
    # exceeds the reaper's threshold.
    old_run_id = uuid.uuid4()
    fresh_run_id = uuid.uuid4()
    long_ago = datetime.now(UTC) - timedelta(hours=2)
    just_now = datetime.now(UTC)
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agent_runs (id, agent_id, a2a_task_id, status, started_at) "
                "VALUES (:id, :a, :t, 'running', :ts)"
            ),
            {"id": old_run_id, "a": agent_id, "t": str(uuid.uuid4()), "ts": long_ago},
        )
        await conn.execute(
            text(
                "INSERT INTO agent_runs (id, agent_id, a2a_task_id, status, started_at) "
                "VALUES (:id, :a, :t, 'running', :ts)"
            ),
            {"id": fresh_run_id, "a": agent_id, "t": str(uuid.uuid4()), "ts": just_now},
        )

    # Run the reaper directly using a session bound to the test engine.
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with sessionmaker() as session:
        reaped = await runs_service.reap_abandoned_runs(session, older_than=timedelta(hours=1))
    assert reaped == 1

    async with db_engine.begin() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, status, stop_reason FROM agent_runs WHERE agent_id = :a ORDER BY started_at"),
                {"a": agent_id},
            )
        ).all()
    by_id = {r.id: r for r in rows}
    assert by_id[old_run_id].status == "failed"
    assert by_id[old_run_id].stop_reason == "abandoned"
    assert by_id[fresh_run_id].status == "running"
    assert by_id[fresh_run_id].stop_reason is None
