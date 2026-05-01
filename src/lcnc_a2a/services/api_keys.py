"""Per-agent API key generation and persistence helpers."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.models.agent_api_key import AgentApiKey

API_KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class GeneratedApiKey:
    """A freshly minted plain key plus its derived storage fields."""

    plain: str
    key_hash: bytes
    key_last4: str


def generate_api_key() -> GeneratedApiKey:
    """Generate a 32-byte base64url API key (~43 chars) and its derived fields."""
    plain = secrets.token_urlsafe(API_KEY_BYTES)
    key_hash = hashlib.sha256(plain.encode("utf-8")).digest()
    key_last4 = plain[-4:]
    return GeneratedApiKey(plain=plain, key_hash=key_hash, key_last4=key_last4)


async def create_agent_api_key(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    label: str = "default",
) -> tuple[AgentApiKey, str]:
    """Generate, persist, and return the new key row plus the plain key (only here)."""
    generated = generate_api_key()
    row = AgentApiKey(
        agent_id=agent_id,
        label=label,
        key_hash=generated.key_hash,
        key_last4=generated.key_last4,
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row, generated.plain
