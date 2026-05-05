"""Generic key/value table for runtime app state and secrets bootstrap."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lcnc_a2a.models.base import Base


class AppState(Base):
    """A single row holds either a plaintext setting or a Fernet-encrypted secret.

    Rows currently used:
      - ``encryption_key_fingerprint`` (plaintext) - sha256(key)[:16] hex digest
        used to detect encryption-key changes between runs.
      - ``session_secret`` (encrypted) - randomly generated on first boot,
        used by ``itsdangerous`` to sign cookies and CSRF tokens.
    """

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(length=120), primary_key=True)
    value: Mapped[str] = mapped_column(Text(), nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
