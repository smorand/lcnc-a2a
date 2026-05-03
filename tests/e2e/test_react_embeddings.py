"""US-006 acceptance tests for the embedding retry policy (E2E-065)."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import (
    StubLlm,
    fetch_runs_for_agent,
    install_llm_mock,
    make_a2a_envelope,
    post_a2a,
)
from tests.e2e._react_helpers import (
    StubEmbedding,
    add_react_tool_call,
    install_embedding_mock,
    seed_started_react_agent,
)
from tests.e2e.test_a2a_react import _seed_mcp_with_noop


@pytest.mark.asyncio
async def test_e2e_065_embedding_503_three_failures_hard_fail(
    seed_user,
    db_engine: AsyncEngine,
    http_client: httpx.AsyncClient,
    respx_mock,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 HTTP 503 calls to /embeddings end the run as failed/embedding_unavailable."""
    from lcnc_a2a.llm import embeddings as embeddings_module

    monkeypatch.setattr(embeddings_module, "EMBED_RETRY_BACKOFFS", (0.001, 0.001, 0.001))

    user_id = await seed_user("alice@example.com", "Alice")
    agent_id, plain = await seed_started_react_agent(
        db_engine, user_id=user_id, max_loops=10, similarity_threshold=0.95
    )
    touch_file = tmp_path / "noop.log"
    await _seed_mcp_with_noop(db_engine, agent_id, str(touch_file))

    # Two iterations are required before similarity check fires.
    stub = StubLlm()
    add_react_tool_call(stub, thought="iter1 thought", tool_name="noop", arguments={})
    add_react_tool_call(stub, thought="iter2 thought", tool_name="noop", arguments={})
    install_llm_mock(respx_mock, stub)

    embed_stub = StubEmbedding(repeat_last=True)
    embed_stub.add_status(status=503)  # all calls 503
    install_embedding_mock(respx_mock, embed_stub)

    status, events, _ = await post_a2a(http_client, agent_id=agent_id, plain_key=plain, body=make_a2a_envelope("hi"))
    assert status == 200

    runs = await fetch_runs_for_agent(db_engine, agent_id)
    run = runs[0]
    assert run["status"] == "failed"
    assert run["stop_reason"] == "embedding_unavailable"

    # Exactly 3 embedding HTTP calls (1 + 2 retries).
    assert len(embed_stub.calls) == 3

    last = events[-1]
    assert last == {
        "event": "TaskStatusUpdate",
        "state": "failed",
        "reason": "embedding_unavailable",
    }
