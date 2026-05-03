"""Mode → executor dispatcher."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.executors.base import ExecutorContext
from lcnc_a2a.executors.simple import SimpleExecutor
from lcnc_a2a.llm.provider import LlmProvider, get_provider


def dispatch(
    *,
    mode: str,
    db: AsyncSession,
    crypto: CryptoService,
    provider: LlmProvider | None = None,
) -> SimpleExecutor:
    """Return the executor instance for ``mode`` (only Simple in US-005)."""
    if mode == "simple":
        snapshot_provider = provider or get_provider("openrouter")
        return SimpleExecutor(db=db, provider=snapshot_provider, crypto=crypto)
    raise NotImplementedError(f"mode_not_implemented:{mode}")


async def stream_run(
    *,
    executor: SimpleExecutor,
    ctx: ExecutorContext,
) -> AsyncIterator[bytes]:
    async for chunk in executor.run(ctx):
        yield chunk
