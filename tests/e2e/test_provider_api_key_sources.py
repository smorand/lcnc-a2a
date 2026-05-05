"""Tests for the three provider-API-key sources: input, env_snapshot, env_dynamic."""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.models.agent import Agent
from lcnc_a2a.routes.a2a import _ProviderKeyEnvMissing, _resolve_provider_key
from lcnc_a2a.schemas.agent_form import AgentFormError, validate_create_agent_form


def _form_kwargs(**overrides: str) -> dict[str, str]:
    base: dict[str, str] = {
        "name": "tester",
        "description": "",
        "mode": "simple",
        "model_provider": "openrouter",
        "model_endpoint": "https://openrouter.ai/api/v1",
        "model_id": "anthropic/claude-sonnet-4-5",
        "provider_api_key": "",
        "api_key_source": "input",
        "system_prompt": "you are helpful.",
        "planner_prompt": "",
        "executor_prompt": "",
        "max_loops": "",
        "max_tokens": "",
        "similarity_threshold": "",
        "max_steps": "",
    }
    base.update(overrides)
    return base


def test_form_env_snapshot_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-from-env-12345")
    data = validate_create_agent_form(**_form_kwargs(api_key_source="env_snapshot"))
    assert data.provider_api_key == "sk-or-from-env-12345"
    assert data.provider_api_key_env_var is None


def test_form_env_snapshot_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(AgentFormError) as exc:
        validate_create_agent_form(**_form_kwargs(api_key_source="env_snapshot"))
    assert exc.value.code == "api_key_env_not_found"


def test_form_env_dynamic_stores_env_var_name() -> None:
    data = validate_create_agent_form(**_form_kwargs(api_key_source="env_dynamic"))
    assert data.provider_api_key == ""
    assert data.provider_api_key_env_var == "OPENROUTER_API_KEY"


def test_form_env_source_other_provider_requires_env_var_name() -> None:
    with pytest.raises(AgentFormError) as exc:
        validate_create_agent_form(
            **_form_kwargs(
                model_provider="openai_compatible",
                model_endpoint="https://api.example.com/v1",
                api_key_source="env_dynamic",
                provider_api_key_env_var_name="",
            ),
        )
    assert exc.value.code == "api_key_env_var_name_required"


def test_form_env_dynamic_other_provider_with_custom_env_var_name() -> None:
    data = validate_create_agent_form(
        **_form_kwargs(
            model_provider="openai_compatible",
            model_endpoint="https://api.example.com/v1",
            api_key_source="env_dynamic",
            provider_api_key_env_var_name="MY_LLM_API_KEY",
        ),
    )
    assert data.provider_api_key_env_var == "MY_LLM_API_KEY"
    assert data.provider_api_key == ""


def test_form_env_snapshot_other_provider_reads_custom_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_LLM_API_KEY", "sk-custom-12345")
    data = validate_create_agent_form(
        **_form_kwargs(
            model_provider="openai_compatible",
            model_endpoint="https://api.example.com/v1",
            api_key_source="env_snapshot",
            provider_api_key_env_var_name="MY_LLM_API_KEY",
        ),
    )
    assert data.provider_api_key == "sk-custom-12345"
    assert data.provider_api_key_env_var is None


def test_form_env_var_name_invalid_chars_rejected() -> None:
    with pytest.raises(AgentFormError) as exc:
        validate_create_agent_form(
            **_form_kwargs(
                model_provider="openai_compatible",
                model_endpoint="https://api.example.com/v1",
                api_key_source="env_dynamic",
                provider_api_key_env_var_name="bad name!",
            ),
        )
    assert exc.value.code == "api_key_env_var_name_invalid"


def test_form_localhost_skips_api_key_requirement() -> None:
    data = validate_create_agent_form(
        **_form_kwargs(
            model_provider="openai_compatible",
            model_endpoint="http://localhost:9121/v1",
            provider_api_key="",
            api_key_source="input",
        ),
    )
    assert data.provider_api_key == ""
    assert data.provider_api_key_env_var is None


def test_resolve_provider_key_decrypts_stored_ciphertext() -> None:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    crypto = CryptoService(key)
    agent = Agent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="x",
        mode="simple",
        model_provider="openrouter",
        model_endpoint="https://openrouter.ai/api/v1",
        model_id="m",
        provider_api_key_enc=crypto.encrypt(b"plain-key"),
        provider_api_key_env_var=None,
        max_loops=10,
        max_tokens=8000,
        status="started",
    )
    assert _resolve_provider_key(agent, crypto) == "plain-key"


def test_resolve_provider_key_reads_env_when_marker_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    crypto = CryptoService(Fernet.generate_key().decode())
    monkeypatch.setenv("OPENROUTER_API_KEY", "live-key-from-env")
    agent = Agent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="x",
        mode="simple",
        model_provider="openrouter",
        model_endpoint="https://openrouter.ai/api/v1",
        model_id="m",
        provider_api_key_enc=None,
        provider_api_key_env_var="OPENROUTER_API_KEY",
        max_loops=10,
        max_tokens=8000,
        status="started",
    )
    assert _resolve_provider_key(agent, crypto) == "live-key-from-env"


def test_resolve_provider_key_raises_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    crypto = CryptoService(Fernet.generate_key().decode())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    agent = Agent(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="x",
        mode="simple",
        model_provider="openrouter",
        model_endpoint="https://openrouter.ai/api/v1",
        model_id="m",
        provider_api_key_enc=None,
        provider_api_key_env_var="OPENROUTER_API_KEY",
        max_loops=10,
        max_tokens=8000,
        status="started",
    )
    with pytest.raises(_ProviderKeyEnvMissing) as exc:
        _resolve_provider_key(agent, crypto)
    assert exc.value.var == "OPENROUTER_API_KEY"


async def test_create_agent_with_env_dynamic_persists_env_var(
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    csrf_token: str,
    seed_user,  # type: ignore[no-untyped-def]
    login_as,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-env-key")
    await login_as("dyn@example.com", name="Dyn")
    response = await http_client.post(
        "/agents",
        data={
            "csrf_token": csrf_token,
            "name": "dyn-agent",
            "description": "",
            "mode": "simple",
            "model_provider": "openrouter",
            "model_endpoint": "https://openrouter.ai/api/v1",
            "model_id": "anthropic/claude-sonnet-4-5",
            "provider_api_key": "",
            "api_key_source": "env_dynamic",
            "system_prompt": "be helpful",
            "planner_prompt": "",
            "executor_prompt": "",
            "max_loops": "",
            "max_tokens": "",
            "similarity_threshold": "",
            "max_steps": "",
        },
    )
    assert response.status_code == 302, response.text

    async with db_engine.begin() as conn:
        row = await conn.execute(
            text("SELECT provider_api_key_env_var, provider_api_key_enc FROM agents WHERE name = :name"),
            {"name": "dyn-agent"},
        )
        env_var, enc = row.one()
    assert env_var == "OPENROUTER_API_KEY"
    assert enc is None or enc == b""
