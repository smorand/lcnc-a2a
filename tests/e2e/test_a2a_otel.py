"""US-005 OpenTelemetry redaction tests (E2E-057, 098)."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    install_llm_mock,
    make_a2a_envelope,
    post_a2a,
    seed_started_agent,
)

PROMPT_TOKEN = "unique-secret-PROMPT-token-XYZ"
RESPONSE_TOKEN = "unique-secret-RESPONSE-token-ABC"


def _reload_with_trace(tmp_path: Path) -> tuple[Path, object]:
    trace_file = tmp_path / "trace.jsonl"
    os.environ["LCNC_A2A_TRACE_FILE"] = str(trace_file)
    # Force tracer re-init by resetting both the module-level flag and the OTel global.
    from opentelemetry import trace as _otel_trace

    import lcnc_a2a.observability.otel as otel_module

    otel_module._provider_initialized = False
    _otel_trace._TRACER_PROVIDER = None
    _otel_trace._TRACER_PROVIDER_SET_ONCE._done = False

    import lcnc_a2a.main as main_module

    importlib.reload(main_module)
    return trace_file, main_module.app


@pytest.mark.asyncio
async def test_e2e_057_trace_redaction(
    seed_user,
    db_engine: AsyncEngine,
    respx_mock,
    tmp_path: Path,
) -> None:
    trace_file, app = _reload_with_trace(tmp_path)

    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_agent(db_engine, user_id=user_id, name="agent-A")

    stub = StubLlm()
    stub.add_text(f"reply containing {RESPONSE_TOKEN}", cost=0.0001)
    install_llm_mock(respx_mock, stub)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        status, events, _ = await post_a2a(
            client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope(f"prompt with {PROMPT_TOKEN}"),
        )
    assert status == 200
    assert events[-1]["state"] == "completed"

    # Force flush of the BatchSpanProcessor
    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)

    assert trace_file.exists()
    contents = trace_file.read_text(encoding="utf-8")
    assert PROMPT_TOKEN not in contents
    assert RESPONSE_TOKEN not in contents

    import json

    chat_lines = [
        json.loads(line) for line in contents.splitlines() if line and json.loads(line)["name"].startswith("llm.chat")
    ]
    assert chat_lines, contents
    expected = {"model", "provider", "tokens.prompt", "tokens.completion", "cost.usd", "duration.ms", "request_id"}
    for span in chat_lines:
        assert set(span["attributes"].keys()).issubset(expected)


@pytest.mark.asyncio
async def test_e2e_098_trace_redaction_consolidated(
    seed_user,
    db_engine: AsyncEngine,
    respx_mock,
    tmp_path: Path,
) -> None:
    """Same shape as E2E-057; recorded under a separate id for the security suite."""
    await test_e2e_057_trace_redaction(seed_user, db_engine, respx_mock, tmp_path)
