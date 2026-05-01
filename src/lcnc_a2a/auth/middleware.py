"""Request-time helpers for fetching the current authenticated user."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.session import SESSION_COOKIE_NAME, SessionManager
from lcnc_a2a.deps import get_db, get_session_manager
from lcnc_a2a.models.user import User


async def fetch_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    sessions: SessionManager = Depends(get_session_manager),
) -> User | None:
    """Return the user attached to the signed session cookie, or None."""
    raw_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_cookie is None:
        return None
    session_id = sessions.verify(raw_cookie)
    if session_id is None:
        return None
    return await sessions.lookup(db, session_id)
