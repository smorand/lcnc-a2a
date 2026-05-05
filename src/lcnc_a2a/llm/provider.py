"""LLM provider ABC and concrete implementations.

Built on raw ``httpx`` (no LLM SDK per US-005 constraints).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx


class LlmProviderError(Exception):
    """Raised when a chat call fails (network or non-2xx)."""


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """A single ``chat.completions`` response."""

    content: str
    tool_calls: list[dict[str, Any]]
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal | None
    request_id: str | None
    raw: dict[str, Any]


class LlmProvider(abc.ABC):
    """Abstract LLM provider."""

    name: str = "unknown"

    @abc.abstractmethod
    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_id: str,
        endpoint: str,
        api_key: str,
        max_tokens: int,
    ) -> ChatResponse:
        """Run a single chat completion."""


class OpenRouterProvider(LlmProvider):
    """OpenRouter chat completions client (cost is read from ``usage.cost``)."""

    __slots__ = ("_client",)

    name: str = "openrouter"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_id: str,
        endpoint: str,
        api_key: str,
        max_tokens: int,
    ) -> ChatResponse:
        return await _post_chat(
            client=self._client,
            messages=messages,
            tools=tools,
            model_id=model_id,
            endpoint=endpoint,
            api_key=api_key,
            max_tokens=max_tokens,
            include_cost=True,
        )


class OpenAiCompatibleProvider(LlmProvider):
    """OpenAI-compatible chat completions client (cost is always ``None``)."""

    __slots__ = ("_client",)

    name: str = "openai_compatible"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model_id: str,
        endpoint: str,
        api_key: str,
        max_tokens: int,
    ) -> ChatResponse:
        return await _post_chat(
            client=self._client,
            messages=messages,
            tools=tools,
            model_id=model_id,
            endpoint=endpoint,
            api_key=api_key,
            max_tokens=max_tokens,
            include_cost=False,
        )


def get_provider(provider: str) -> LlmProvider:
    """Return a provider instance by name."""
    if provider == "openrouter":
        return OpenRouterProvider()
    if provider == "openai_compatible":
        return OpenAiCompatibleProvider()
    raise LlmProviderError(f"unknown_provider:{provider}")


async def _post_chat(
    *,
    client: httpx.AsyncClient | None,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model_id: str,
    endpoint: str,
    api_key: str,
    max_tokens: int,
    include_cost: bool,
) -> ChatResponse:
    # ``max_tokens`` is the agent's cumulative output budget; the executors
    # enforce it across loops. We deliberately do not forward it as a per-call
    # cap so each LLM call uses the server's default. Forwarding e.g. 1_000_000
    # to a local mlx_lm makes it allocate that budget literally and stall.
    del max_tokens
    body: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
    if include_cost:
        body["usage"] = {"include": True}

    url = f"{endpoint.rstrip('/')}/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        if own_client:
            await client.aclose()
        raise LlmProviderError(f"transport_error:{exc}") from exc

    try:
        if response.status_code >= 500 or response.status_code >= 400:
            raise LlmProviderError(f"llm_status_{response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise LlmProviderError("llm_invalid_json") from exc
    finally:
        if own_client:
            await client.aclose()

    return _parse_response(payload, include_cost=include_cost)


def _parse_response(payload: dict[str, Any], *, include_cost: bool) -> ChatResponse:
    choices = payload.get("choices") or []
    if not choices:
        raise LlmProviderError("llm_empty_choices")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    raw_tool_calls = message.get("tool_calls") or []
    tool_calls: list[dict[str, Any]] = []
    for call in raw_tool_calls:
        if not isinstance(call, dict):
            continue
        tool_calls.append(call)

    usage = payload.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    cost: Decimal | None = None
    if include_cost and "cost" in usage and usage["cost"] is not None:
        cost = Decimal(str(usage["cost"]))
    request_id = payload.get("id")
    return ChatResponse(
        content=content if isinstance(content, str) else "",
        tool_calls=tool_calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        request_id=str(request_id) if request_id else None,
        raw=payload,
    )
