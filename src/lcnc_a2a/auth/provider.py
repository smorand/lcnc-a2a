"""Authentication provider abstract base class."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """A successfully authenticated user."""

    id: uuid.UUID
    email: str
    name: str


class AuthProvider(ABC):
    """ABC for authentication providers (dev, OAuth2, etc.)."""

    @abstractmethod
    async def authenticate(
        self,
        db: AsyncSession,
        *,
        email: str,
        name: str,
    ) -> AuthenticatedUser:
        """Authenticate a user and upsert into the users table."""
