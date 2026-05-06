"""AgentMessage model (skeletal; full behavior lands in US-005)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from lcnc_a2a.models.base import Base
from lcnc_a2a.models.types import JsonField, PkUuid


class AgentMessage(Base):
    """A single message inside an A2A context."""

    __tablename__ = "agent_messages"
    __table_args__ = (Index("ix_agent_messages_context_id_position", "context_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        primary_key=True,
        default=uuid.uuid4,
    )
    context_id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        ForeignKey("agent_contexts.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_call_json: Mapped[Any | None] = mapped_column(JsonField(), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
