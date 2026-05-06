"""Session model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from lcnc_a2a.models.base import Base
from lcnc_a2a.models.types import PkUuid


class Session(Base):
    """A signed session keyed by UUID; cookie carries the signed UUID."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
