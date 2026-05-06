"""AgentRun model (full schema for US-005)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lcnc_a2a.models.base import Base
from lcnc_a2a.models.types import JsonField, PkUuid


class AgentRun(Base):
    """A single execution of an agent."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_agent_id_started_at", "agent_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        primary_key=True,
        default=uuid.uuid4,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    context_id: Mapped[uuid.UUID | None] = mapped_column(
        PkUuid(),
        ForeignKey("agent_contexts.id", ondelete="SET NULL"),
        nullable=True,
    )
    a2a_task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    stop_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loops: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    plan: Mapped[Any | None] = mapped_column(JsonField(), nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_snapshot: Mapped[Any | None] = mapped_column(JsonField(), nullable=True)
    # Snapshot of a tool call awaiting user confirmation (TASK_STATE_INPUT_REQUIRED).
    # Set when status='paused'; consumed and cleared on resume.
    pending_action: Mapped[Any | None] = mapped_column(JsonField(), nullable=True)
