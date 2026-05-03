"""Mode → executor dispatcher."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.crypto import CryptoService
from lcnc_a2a.executors.base import ExecutorContext
from lcnc_a2a.executors.plan_execute import PlanExecuteExecutor
from lcnc_a2a.executors.react import ReActExecutor
from lcnc_a2a.executors.simple import SimpleExecutor
from lcnc_a2a.llm.provider import LlmProvider, get_provider

Executor = SimpleExecutor | ReActExecutor | PlanExecuteExecutor


def dispatch(
    *,
    mode: str,
    db: AsyncSession,
    crypto: CryptoService,
    provider: LlmProvider | None = None,
) -> Executor:
    """Return the executor instance for ``mode``."""
    if mode == "simple":
        snapshot_provider = provider or get_provider("openrouter")
        return SimpleExecutor(db=db, provider=snapshot_provider, crypto=crypto)
    if mode == "react":
        snapshot_provider = provider or get_provider("openrouter")
        return ReActExecutor(db=db, provider=snapshot_provider, crypto=crypto)
    if mode == "plan_execute":
        snapshot_provider = provider or get_provider("openrouter")
        return PlanExecuteExecutor(db=db, provider=snapshot_provider, crypto=crypto)
    raise NotImplementedError(f"mode_not_implemented:{mode}")


async def stream_run(
    *,
    executor: Executor,
    ctx: ExecutorContext,
) -> AsyncIterator[bytes]:
    async for chunk in executor.run(ctx):
        yield chunk
