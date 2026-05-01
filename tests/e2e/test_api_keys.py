"""Acceptance tests for API-key generation and secret handling (E2E-017, E2E-018, E2E-097)."""

from __future__ import annotations

import hashlib
import re

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

REACT_PROMPT = "ReAct prompt for tests"


async def _create_agent(client: httpx.AsyncClient, *, csrf: str, name: str, provider_key: str) -> tuple[str, str]:
    response = await client.post(
        "/agents",
        data={
            "name": name,
            "mode": "react",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": provider_key,
            "system_prompt": REACT_PROMPT,
            "max_loops": "10",
            "max_tokens": "8000",
            "similarity_threshold": "0.95",
            "csrf_token": csrf,
        },
    )
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    detail = await client.get(location)
    assert detail.status_code == 200
    plain_keys = re.findall(r'data-copy-target="([A-Za-z0-9_-]{43})"', detail.text)
    assert len(plain_keys) == 1, detail.text
    return location, plain_keys[0]


async def test_e2e_017_api_key_persisted_as_hash_and_last4(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-017: agent_api_keys stores sha256 hash + last 4; plain key absent from agents."""
    client = await login_as("alice@example.com", name="Alice")
    _location, plain_key = await _create_agent(client, csrf=csrf_token, name="hash-test", provider_key="sk-secret")

    async with db_engine.begin() as conn:
        keys = await conn.execute(text("SELECT key_hash, key_last4 FROM agent_api_keys"))
        rows = list(keys.mappings())
        assert len(rows) == 1
        assert bytes(rows[0]["key_hash"]) == hashlib.sha256(plain_key.encode()).digest()
        assert rows[0]["key_last4"] == plain_key[-4:]

        agents = await conn.execute(
            text(
                "SELECT name, description, mode, model_provider, model_endpoint, "
                "model_id, system_prompt, planner_prompt, executor_prompt, status "
                "FROM agents"
            )
        )
        agent = agents.mappings().one()
        concatenated = " ".join("" if value is None else str(value) for value in agent.values())
        assert plain_key not in concatenated


async def test_e2e_018_provider_api_key_fernet_round_trip(
    login_as,
    csrf_token: str,
    db_engine: AsyncEngine,
) -> None:
    """E2E-018: provider_api_key_enc decrypts back to the exact plaintext."""
    client = await login_as("alice@example.com", name="Alice")
    await _create_agent(client, csrf=csrf_token, name="rt-test", provider_key="sk-or-v1-roundtrip")

    transport = client._transport
    asgi_app = transport.app  # type: ignore[attr-defined]
    crypto = asgi_app.state.crypto

    async with db_engine.begin() as conn:
        row = await conn.execute(text("SELECT provider_api_key_enc FROM agents"))
        enc = bytes(row.scalar_one())

    assert crypto.decrypt(enc).decode() == "sk-or-v1-roundtrip"


async def test_e2e_097_provider_api_key_never_in_response(
    login_as,
    csrf_token: str,
) -> None:
    """E2E-097: detail page never echoes the provider key; renders `********`."""
    client = await login_as("alice@example.com", name="Alice")
    location, _plain = await _create_agent(
        client,
        csrf=csrf_token,
        name="leak-test",
        provider_key="unique-secret-PROV-token-789",
    )

    detail = await client.get(location)
    assert detail.status_code == 200
    body = detail.text
    headers_concat = " ".join(f"{k}: {v}" for k, v in detail.headers.items())
    full = body + " " + headers_concat
    assert "unique-secret-PROV-token-789" not in full
    assert "********" in body
