"""Signed session cookies via itsdangerous."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.models.session import Session as SessionModel
from lcnc_a2a.models.user import User

SESSION_COOKIE_NAME = "session"


class SessionManager:
    """Mints and validates signed session cookies."""

    __slots__ = ("_expiry", "_serializer")

    def __init__(self, secret: str, *, expiry_hours: int = 24) -> None:
        self._serializer = URLSafeSerializer(secret, salt="session")
        self._expiry = timedelta(hours=expiry_hours)

    def sign(self, session_id: uuid.UUID) -> str:
        """Produce a signed cookie value for a session id."""
        return self._serializer.dumps(str(session_id))

    def verify(self, cookie_value: str) -> uuid.UUID | None:
        """Return the session UUID for a valid signed cookie, else None."""
        try:
            raw = self._serializer.loads(cookie_value)
        except BadSignature:
            return None
        try:
            return uuid.UUID(str(raw))
        except (TypeError, ValueError):
            return None

    async def create(self, db: AsyncSession, user_id: uuid.UUID) -> SessionModel:
        """Persist a new session row and return it."""
        sess = SessionModel(
            user_id=user_id,
            expires_at=datetime.now(UTC) + self._expiry,
        )
        db.add(sess)
        await db.flush()
        await db.refresh(sess)
        return sess

    async def lookup(self, db: AsyncSession, session_id: uuid.UUID) -> User | None:
        """Return the User for a valid, unexpired session."""
        result = await db.execute(select(SessionModel).where(SessionModel.id == session_id))
        sess = result.scalar_one_or_none()
        if sess is None:
            return None
        if sess.expires_at <= datetime.now(UTC):
            return None
        user_result = await db.execute(select(User).where(User.id == sess.user_id))
        return user_result.scalar_one_or_none()
