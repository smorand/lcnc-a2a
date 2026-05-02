"""Acceptance tests for the delete-agent flow (E2E-030..034)."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _csrf(client: httpx.AsyncClient) -> str:
    response = await client.get("/login")
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


async def _seed_full_cascade(db_engine: AsyncEngine, agent_id: uuid.UUID) -> dict[str, list[uuid.UUID]]:
    """Insert 2 keys, 1 mcp, 3 runs (with 2 steps each), 2 contexts (with 2 messages each)."""
    now = datetime.now(UTC)
    api_key_ids = [uuid.uuid4() for _ in range(2)]
    mcp_id = uuid.uuid4()
    run_ids = [uuid.uuid4() for _ in range(3)]
    step_ids = [uuid.uuid4() for _ in range(6)]
    context_ids = [uuid.uuid4() for _ in range(2)]
    message_ids = [uuid.uuid4() for _ in range(4)]

    async with db_engine.begin() as conn:
        for i, kid in enumerate(api_key_ids):
            await conn.execute(
                text(
                    "INSERT INTO agent_api_keys (id, agent_id, label, key_hash, key_last4) "
                    "VALUES (:id, :agent_id, :label, :key_hash, :last4)"
                ),
                {
                    "id": kid,
                    "agent_id": agent_id,
                    "label": f"k{i}",
                    "key_hash": f"hash-{i}".encode().ljust(32, b"\x00"),
                    "last4": f"abc{i}",
                },
            )
        await conn.execute(
            text(
                "INSERT INTO agent_mcp_servers (id, agent_id, transport, tool_timeout_s) "
                "VALUES (:id, :agent_id, 'stdio', 30)"
            ),
            {"id": mcp_id, "agent_id": agent_id},
        )
        for rid in run_ids:
            await conn.execute(
                text(
                    "INSERT INTO agent_runs (id, agent_id, status, started_at) "
                    "VALUES (:id, :agent_id, 'completed', :started_at)"
                ),
                {"id": rid, "agent_id": agent_id, "started_at": now},
            )
        for i, sid in enumerate(step_ids):
            await conn.execute(
                text(
                    "INSERT INTO agent_run_steps (id, run_id, seq, role, occurred_at, truncated) "
                    "VALUES (:id, :run_id, :seq, 'thought', :occurred_at, false)"
                ),
                {
                    "id": sid,
                    "run_id": run_ids[i // 2],
                    "seq": i % 2,
                    "occurred_at": now,
                },
            )
        for cid in context_ids:
            await conn.execute(
                text("INSERT INTO agent_contexts (id, agent_id, message_count) VALUES (:id, :agent_id, 0)"),
                {"id": cid, "agent_id": agent_id},
            )
        for i, mid in enumerate(message_ids):
            await conn.execute(
                text(
                    "INSERT INTO agent_messages (id, context_id, role, content, position) "
                    "VALUES (:id, :context_id, 'user', :content, :position)"
                ),
                {
                    "id": mid,
                    "context_id": context_ids[i // 2],
                    "content": f"m{i}",
                    "position": i % 2,
                },
            )

    return {
        "api_keys": api_key_ids,
        "mcp": [mcp_id],
        "runs": run_ids,
        "steps": step_ids,
        "contexts": context_ids,
        "messages": message_ids,
    }


async def _create_agent(client: httpx.AsyncClient, csrf: str, name: str = "agent-a") -> uuid.UUID:
    response = await client.post(
        "/agents",
        data={
            "name": name,
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "K1",
            "system_prompt": "old",
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 302, response.text
    return uuid.UUID(response.headers["location"].rsplit("/", 1)[-1])


async def test_e2e_030_delete_happy_path_with_cascade(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-030: deleting an agent removes the row and every cascade-target row."""
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(client, csrf_token)
    await _seed_full_cascade(db_engine, agent_id)

    response = await client.post(
        f"/agents/{agent_id}",
        data={"_method": "DELETE", "csrf_token": csrf_token},
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/agents"

    async with db_engine.begin() as conn:
        for table, where in [
            ("agents", "id = :id"),
            ("agent_api_keys", "agent_id = :id"),
            ("agent_mcp_servers", "agent_id = :id"),
            ("agent_runs", "agent_id = :id"),
            ("agent_contexts", "agent_id = :id"),
        ]:
            count = await conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {where}"),
                {"id": agent_id},
            )
            assert count.scalar_one() == 0, table

        steps = await conn.execute(
            text("SELECT count(*) FROM agent_run_steps s JOIN agent_runs r ON s.run_id = r.id WHERE r.agent_id = :id"),
            {"id": agent_id},
        )
        assert steps.scalar_one() == 0
        messages = await conn.execute(
            text(
                "SELECT count(*) FROM agent_messages m "
                "JOIN agent_contexts c ON m.context_id = c.id WHERE c.agent_id = :id"
            ),
            {"id": agent_id},
        )
        assert messages.scalar_one() == 0


async def test_e2e_031_delete_returns_404_for_other_user(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-031: deleting another user's agent returns 404; row remains."""
    alice = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(alice, csrf_token)

    bob = await login_as("bob@example.com", name="Bob")
    bob_csrf = await _csrf(bob)

    response = await bob.post(
        f"/agents/{agent_id}",
        data={"_method": "DELETE", "csrf_token": bob_csrf},
    )
    assert response.status_code == 404

    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT count(*) FROM agents WHERE id = :id"), {"id": agent_id})
        assert row.scalar_one() == 1


async def test_e2e_032_delete_unknown_returns_404(
    login_as,
    csrf_token: str,
) -> None:
    """E2E-032: deleting an unknown id returns 404."""
    client = await login_as("alice@example.com", name="Alice")
    response = await client.post(
        f"/agents/{uuid.uuid4()}",
        data={"_method": "DELETE", "csrf_token": csrf_token},
    )
    assert response.status_code == 404


async def test_e2e_034_delete_cascade_full_data_integrity(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-034: count(*) == 0 in every dependent table (transitive too) post-delete."""
    client = await login_as("alice@example.com", name="Alice")
    agent_id = await _create_agent(client, csrf_token)
    seeded = await _seed_full_cascade(db_engine, agent_id)
    assert sum(len(v) for v in seeded.values()) > 0

    response = await client.post(
        f"/agents/{agent_id}",
        data={"_method": "DELETE", "csrf_token": csrf_token},
    )
    assert response.status_code == 302

    async with db_engine.begin() as conn:
        checks = [
            ("agent_api_keys", "agent_id = :id"),
            ("agent_mcp_servers", "agent_id = :id"),
            ("agent_runs", "agent_id = :id"),
            ("agent_contexts", "agent_id = :id"),
        ]
        for table, where in checks:
            count = await conn.execute(
                text(f"SELECT count(*) FROM {table} WHERE {where}"),
                {"id": agent_id},
            )
            assert count.scalar_one() == 0, table

        joined_steps = await conn.execute(
            text("SELECT count(*) FROM agent_run_steps s JOIN agent_runs r ON s.run_id = r.id WHERE r.agent_id = :id"),
            {"id": agent_id},
        )
        assert joined_steps.scalar_one() == 0
        joined_msgs = await conn.execute(
            text(
                "SELECT count(*) FROM agent_messages m "
                "JOIN agent_contexts c ON m.context_id = c.id WHERE c.agent_id = :id"
            ),
            {"id": agent_id},
        )
        assert joined_msgs.scalar_one() == 0

        orphan_steps = await conn.execute(
            text("SELECT count(*) FROM agent_run_steps WHERE id = ANY(:ids)"),
            {"ids": seeded["steps"]},
        )
        assert orphan_steps.scalar_one() == 0
        orphan_msgs = await conn.execute(
            text("SELECT count(*) FROM agent_messages WHERE id = ANY(:ids)"),
            {"ids": seeded["messages"]},
        )
        assert orphan_msgs.scalar_one() == 0
