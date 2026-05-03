"""Embedding provider client with retry policy (FR-019).

Built on raw ``httpx`` to mirror :mod:`lcnc_a2a.llm.provider` and avoid any
third-party LLM SDK.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import httpx

DEFAULT_OPENROUTER_EMBEDDING_MODEL = "openai/text-embedding-3-small"
EMBED_RETRY_BACKOFFS: tuple[float, ...] = (0.2, 0.6, 1.8)


class EmbeddingError(Exception):
    """Raised when an embedding call fails (and retries are exhausted)."""


class _EmbeddingRetryable(Exception):
    """Internal: signals a retryable failure (transport / 5xx / 429)."""


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """A single embeddings response."""

    vector: list[float]
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal | None
    request_id: str | None


def resolve_embedding_model(*, provider: str, agent_embedding_model: str | None) -> str:
    """Pick the embedding model id based on provider + agent override."""
    if agent_embedding_model:
        return agent_embedding_model
    if provider == "openrouter":
        return DEFAULT_OPENROUTER_EMBEDDING_MODEL
    return "text-embedding-3-small"


async def embed(
    *,
    text: str,
    model: str,
    endpoint: str,
    api_key: str,
    include_cost: bool = True,
    client: httpx.AsyncClient | None = None,
    backoffs: tuple[float, ...] = EMBED_RETRY_BACKOFFS,
) -> EmbeddingResult:
    """Run ``POST /v1/embeddings`` with the FR-019 retry policy.

    Three total attempts (1 + 2 retries) with backoffs ``200ms / 600ms /
    1800ms``. Retries fire on transport errors, HTTP 5xx, and HTTP 429.
    Other 4xx responses fail immediately (no retry).
    """
    last_error: Exception | None = None
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        for attempt in range(1, len(backoffs) + 1):
            try:
                return await _post_embed(
                    client=client,
                    text=text,
                    model=model,
                    endpoint=endpoint,
                    api_key=api_key,
                    include_cost=include_cost,
                )
            except _EmbeddingRetryable as exc:
                last_error = exc
                if attempt < len(backoffs):
                    await asyncio.sleep(backoffs[attempt - 1])
            except EmbeddingError:
                raise
        raise EmbeddingError(f"embedding_unavailable:{last_error}")
    finally:
        if own_client:
            await client.aclose()


async def _post_embed(
    *,
    client: httpx.AsyncClient,
    text: str,
    model: str,
    endpoint: str,
    api_key: str,
    include_cost: bool,
) -> EmbeddingResult:
    body: dict[str, Any] = {"model": model, "input": text}
    url = f"{endpoint.rstrip('/')}/embeddings"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise _EmbeddingRetryable(f"transport_error:{exc}") from exc

    if response.status_code >= 500 or response.status_code == 429:
        raise _EmbeddingRetryable(f"embedding_status_{response.status_code}")
    if response.status_code >= 400:
        raise EmbeddingError(f"embedding_status_{response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise EmbeddingError("embedding_invalid_json") from exc

    return _parse_response(payload, include_cost=include_cost)


def _parse_response(payload: dict[str, Any], *, include_cost: bool) -> EmbeddingResult:
    data = payload.get("data") or []
    if not data:
        raise EmbeddingError("embedding_empty_data")
    first = data[0]
    if not isinstance(first, dict):
        raise EmbeddingError("embedding_invalid_data")
    raw_vector = first.get("embedding") or []
    if not isinstance(raw_vector, list):
        raise EmbeddingError("embedding_invalid_vector")
    vector: list[float] = []
    for value in raw_vector:
        try:
            vector.append(float(value))
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("embedding_invalid_vector_value") from exc

    usage = payload.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)
    cost: Decimal | None = None
    if include_cost and "cost" in usage and usage["cost"] is not None:
        cost = Decimal(str(usage["cost"]))
    request_id = payload.get("id")
    return EmbeddingResult(
        vector=vector,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        request_id=str(request_id) if request_id else None,
    )


__all__ = [
    "DEFAULT_OPENROUTER_EMBEDDING_MODEL",
    "EMBED_RETRY_BACKOFFS",
    "EmbeddingError",
    "EmbeddingResult",
    "embed",
    "resolve_embedding_model",
]
