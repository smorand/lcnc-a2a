"""Helpers for the US-006 ReAct acceptance tests."""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
import respx
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.e2e._a2a_helpers import LLM_ENDPOINT

LLM_EMBED_URL = f"{LLM_ENDPOINT}/embeddings"


@dataclass
class StubEmbedding:
    """Programmable embeddings mock backed by ``respx``."""

    responses: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    repeat_last: bool = False

    def add_vector(self, vector: list[float], *, prompt_tokens: int = 0, request_id: str | None = None) -> None:
        usage: dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": 0}
        body: dict[str, Any] = {"data": [{"embedding": vector}], "usage": usage}
        if request_id is not None:
            body["id"] = request_id
        self.responses.append(body)

    def add_status(self, *, status: int, body: str = "boom") -> None:
        self.responses.append({"_status": status, "_body": body})

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        body_bytes = await request.aread() if not request.content else request.content
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        index = len(self.calls)
        self.calls.append(payload)
        if index < len(self.responses):
            response = self.responses[index]
        elif self.repeat_last and self.responses:
            response = self.responses[-1]
        else:
            response = {"data": [{"embedding": [0.0, 0.0]}], "usage": {"prompt_tokens": 0}}
        if "_status" in response:
            return httpx.Response(int(response["_status"]), text=str(response.get("_body", "")))
        return httpx.Response(200, json=response)


def install_embedding_mock(mock: respx.Router, stub: StubEmbedding) -> None:
    mock.post(LLM_EMBED_URL).mock(side_effect=stub)


def make_embedding(seed: int, *, dim: int = 1536) -> list[float]:
    """Deterministic 1536-dim float vector keyed off ``seed``."""
    rng = random.Random(seed)
    return [rng.gauss(0.0, 1.0) for _ in range(dim)]


def add_react_tool_call(
    stub: Any,
    *,
    thought: str,
    tool_name: str,
    arguments: dict[str, Any],
    prompt_tokens: int = 5,
    completion_tokens: int = 5,
    cost: float | None = 0.0001,
    call_id: str | None = None,
) -> None:
    """Append a ReAct-style tool_call response to a ``StubLlm``.

    Unlike :meth:`StubLlm.add_tool_call`, the assistant ``content`` carries
    the ReAct ``Thought:`` text so the parser can persist it as the
    iteration's candidate.
    """
    usage: dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cost is not None:
        usage["cost"] = cost
    tool_call_id = call_id or f"call-{len(stub.responses)}"
    stub.responses.append(
        {
            "id": f"resp-{len(stub.responses)}",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"Thought: {thought}",
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": usage,
        }
    )


def add_final_answer(
    stub: Any,
    *,
    text: str,
    prompt_tokens: int = 5,
    completion_tokens: int = 5,
    cost: float | None = 0.0001,
) -> None:
    """Append a ReAct-style final-answer response (``Final Answer: ...``)."""
    usage: dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cost is not None:
        usage["cost"] = cost
    stub.responses.append(
        {
            "id": f"resp-{len(stub.responses)}",
            "choices": [{"message": {"role": "assistant", "content": f"Final Answer: {text}"}}],
            "usage": usage,
        }
    )


def add_unparseable(
    stub: Any,
    *,
    content: str = "garbled output without any structure",
    prompt_tokens: int = 5,
    completion_tokens: int = 5,
    cost: float | None = 0.0001,
) -> None:
    """Append a non-parseable LLM response (no tool_calls, no Final Answer prefix)."""
    usage: dict[str, Any] = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cost is not None:
        usage["cost"] = cost
    stub.responses.append(
        {
            "id": f"resp-{len(stub.responses)}",
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": usage,
        }
    )


async def seed_started_react_agent(
    db_engine: AsyncEngine,
    *,
    user_id: uuid.UUID,
    name: str = "agent-R",
    description: str = "ReAct test agent",
    model_endpoint: str = LLM_ENDPOINT,
    model_provider: str = "openrouter",
    max_loops: int = 10,
    max_tokens: int = 8000,
    similarity_threshold: float = 0.95,
    system_prompt: str = "You are a ReAct agent. Use Thought:/Action: lines and Final Answer: when done.",
) -> tuple[uuid.UUID, str]:
    """Insert a started ReAct agent + one API key; return ``(agent_id, plain_key)``."""
    fernet = Fernet(os.environ["LCNC_A2A_ENCRYPTION_KEY"].encode())
    enc_key = fernet.encrypt(b"sk-fake")
    agent_id = uuid.uuid4()
    plain_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plain_key.encode()).digest()
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO agents (id, user_id, name, description, mode, model_provider, "
                "model_endpoint, model_id, provider_api_key_enc, max_loops, max_tokens, "
                "similarity_threshold, system_prompt, status) VALUES (:id, :user_id, :name, "
                ":description, 'react', :model_provider, :model_endpoint, 'mock-model', :enc, "
                ":max_loops, :max_tokens, :similarity_threshold, :system_prompt, 'started')"
            ),
            {
                "id": agent_id,
                "user_id": user_id,
                "name": name,
                "description": description,
                "model_provider": model_provider,
                "model_endpoint": model_endpoint,
                "enc": enc_key,
                "max_loops": max_loops,
                "max_tokens": max_tokens,
                "similarity_threshold": similarity_threshold,
                "system_prompt": system_prompt,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO agent_api_keys (id, agent_id, label, key_hash, key_last4) "
                "VALUES (:id, :agent_id, 'default', :key_hash, :key_last4)"
            ),
            {
                "id": uuid.uuid4(),
                "agent_id": agent_id,
                "key_hash": key_hash,
                "key_last4": plain_key[-4:],
            },
        )
    return agent_id, plain_key


def encrypt_env(env: dict[str, str]) -> bytes:
    """Encrypt an env dict with the test ``LCNC_A2A_ENCRYPTION_KEY``."""
    fernet = Fernet(os.environ["LCNC_A2A_ENCRYPTION_KEY"].encode())
    return fernet.encrypt(json.dumps(env, sort_keys=True).encode("utf-8"))


__all__ = [
    "LLM_EMBED_URL",
    "StubEmbedding",
    "add_final_answer",
    "add_react_tool_call",
    "add_unparseable",
    "encrypt_env",
    "install_embedding_mock",
    "make_embedding",
    "seed_started_react_agent",
]
