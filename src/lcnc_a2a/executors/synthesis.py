"""Force-synthesis helper used by the ReAct executor (FR-017).

This module is intentionally executor-agnostic so the Plan & Execute
executor (US-007) can reuse the same logic.
"""

from __future__ import annotations

from typing import Any

from lcnc_a2a.llm.provider import ChatResponse, LlmProvider

SYNTHESIS_TEMPLATE = (
    "You ran out of budget without producing a final answer. "
    "Synthesize the best possible final answer from the work so far. "
    "Reply with the answer text only; do NOT call any more tools."
)


def estimate_synthesis_extra_tokens(scratchpad_chars: int) -> int:
    """Rough heuristic for the synthesis call's token cost.

    Approximates the prompt size at ``chars / 4`` tokens. The caller
    decides whether the projected total exceeds ``max_tokens * 1.5`` and
    must abort synthesis.
    """
    return max(0, scratchpad_chars) // 4


def should_skip_synthesis(*, cumulative_tokens: int, max_tokens: int, scratchpad_chars: int) -> bool:
    """Return ``True`` when synthesis would exceed ``max_tokens`` by > 50%."""
    if max_tokens <= 0:
        return False
    estimated = estimate_synthesis_extra_tokens(scratchpad_chars)
    projected_total = cumulative_tokens + estimated
    return projected_total > int(max_tokens * 1.5)


def build_synthesis_messages(
    *,
    system_prompt: str | None,
    user_text: str,
    scratchpad_text: str,
) -> list[dict[str, Any]]:
    """Build the OpenAI ``messages`` payload for the synthesis call."""
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_text})
    messages.append(
        {
            "role": "user",
            "content": SYNTHESIS_TEMPLATE + "\n\nScratchpad:\n" + scratchpad_text,
        }
    )
    return messages


async def run_synthesis(
    *,
    provider: LlmProvider,
    system_prompt: str | None,
    user_text: str,
    scratchpad_text: str,
    model_id: str,
    endpoint: str,
    api_key: str,
    max_tokens: int,
) -> ChatResponse:
    """Run the single synthesis ``chat.completions`` call (no tools)."""
    messages = build_synthesis_messages(
        system_prompt=system_prompt,
        user_text=user_text,
        scratchpad_text=scratchpad_text,
    )
    return await provider.chat(
        messages=messages,
        tools=None,
        model_id=model_id,
        endpoint=endpoint,
        api_key=api_key,
        max_tokens=max_tokens,
    )


__all__ = [
    "SYNTHESIS_TEMPLATE",
    "build_synthesis_messages",
    "estimate_synthesis_extra_tokens",
    "run_synthesis",
    "should_skip_synthesis",
]
