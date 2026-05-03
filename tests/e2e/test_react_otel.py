"""US-006 OTel redaction tests for ReAct embedding spans (E2E-071)."""

from __future__ import annotations

import importlib
import json
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
)
from tests.e2e._react_helpers import (
    StubEmbedding,
    add_react_tool_call,
    install_embedding_mock,
    make_embedding,
    seed_started_react_agent,
)
from tests.e2e.test_a2a_react import _seed_mcp_with_noop

PROMPT_TOKEN = "unique-secret-PROMPT-react-token"
RESPONSE_TOKEN = "unique-secret-RESPONSE-react-token"
EMBED_TOKEN = "unique-secret-EMBED-react-input"


def _reload_with_trace(tmp_path: Path) -> tuple[Path, object]:
    trace_file = tmp_path / "trace.jsonl"
    os.environ["LCNC_A2A_TRACE_FILE"] = str(trace_file)
    from opentelemetry import trace as _otel_trace

    import lcnc_a2a.observability.otel as otel_module

    otel_module._provider_initialized = False
    _otel_trace._TRACER_PROVIDER = None
    _otel_trace._TRACER_PROVIDER_SET_ONCE._done = False

    import lcnc_a2a.main as main_module

    importlib.reload(main_module)
    return trace_file, main_module.app


@pytest.mark.asyncio
async def test_e2e_071_react_trace_redaction(
    seed_user,
    db_engine: AsyncEngine,
    respx_mock,
    tmp_path: Path,
) -> None:
    trace_file, app = _reload_with_trace(tmp_path)

    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=10, similarity_threshold=0.95
    )
    noop_log = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(noop_log))

    stub = StubLlm()
    add_react_tool_call(stub, thought=f"iter1 {EMBED_TOKEN}", tool_name="noop", arguments={})
    add_react_tool_call(stub, thought=f"iter2 {EMBED_TOKEN}", tool_name="noop", arguments={})
    # Final answer carries the response token. The user prompt carries the prompt token.
    stub.responses.append(
        {
            "id": "resp-final",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"Final Answer: reply with {RESPONSE_TOKEN}",
                    }
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "cost": 0.0001},
        }
    )
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding()
    embed_stub.add_vector(make_embedding(seed=1), prompt_tokens=3, request_id="req-embed-1")
    embed_stub.add_vector(make_embedding(seed=2), prompt_tokens=3, request_id="req-embed-2")
    install_embedding_mock(respx_mock, embed_stub)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=False) as client:
        status, events, _ = await post_a2a(
            client,
            agent_id=agent_id,
            plain_key=plain,
            body=make_a2a_envelope(f"prompt with {PROMPT_TOKEN}"),
        )
    assert status == 200, events

    from opentelemetry import trace

    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=5000)

    assert trace_file.exists()
    contents = trace_file.read_text(encoding="utf-8")
    assert PROMPT_TOKEN not in contents
    assert RESPONSE_TOKEN not in contents
    assert EMBED_TOKEN not in contents

    embed_lines = [
        json.loads(line) for line in contents.splitlines() if line and json.loads(line)["name"].startswith("llm.embed")
    ]
    assert embed_lines, contents
    expected_keys = {
        "model",
        "provider",
        "tokens.prompt",
        "tokens.completion",
        "cost.usd",
        "duration.ms",
        "request_id",
    }
    for span in embed_lines:
        assert set(span["attributes"].keys()).issubset(expected_keys), span
