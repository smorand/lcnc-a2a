"""Bearer API-key validation for the A2A surface (FR-012)."""

from __future__ import annotations

import hashlib
import hmac
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.models.agent_api_key import AgentApiKey


def parse_bearer_header(value: str | None) -> str | None:
    """Return the bearer token (anything after ``Bearer ``) or ``None``."""
    if not value:
        return None
    parts = value.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def validate_api_key(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    plain_key: str,
) -> AgentApiKey | None:
    """Constant-time match of ``plain_key`` against the agent's non-revoked keys."""
    result = await db.execute(
        select(AgentApiKey).where(
            AgentApiKey.agent_id == agent_id,
            AgentApiKey.revoked_at.is_(None),
        )
    )
    rows = list(result.scalars().all())
    candidate = hashlib.sha256(plain_key.encode("utf-8")).digest()
    matched: AgentApiKey | None = None
    for row in rows:
        # `compare_digest` handles unequal-length safely; iterate over all rows
        # so we don't short-circuit and leak existence via timing.
        if hmac.compare_digest(row.key_hash, candidate):
            matched = row
    return matched
