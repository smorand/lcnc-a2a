"""Dev mode email-only authentication provider."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lcnc_a2a.auth.provider import AuthenticatedUser, AuthProvider
from lcnc_a2a.models.user import User


class DevModeAuthProvider(AuthProvider):
    """No-credential dev provider: trusts the email/name pair from the form."""

    async def authenticate(
        self,
        db: AsyncSession,
        *,
        email: str,
        name: str,
    ) -> AuthenticatedUser:
        normalized = email.strip().lower()
        result = await db.execute(select(User).where(User.email == normalized))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(email=normalized, name=name)
            db.add(user)
        else:
            user.name = name
        await db.flush()
        await db.refresh(user)
        return AuthenticatedUser(id=user.id, email=user.email, name=user.name)
