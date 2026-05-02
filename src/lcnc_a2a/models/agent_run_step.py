"""AgentRunStep model (skeletal; full behavior lands in US-005)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lcnc_a2a.models.base import Base


class AgentRunStep(Base):
    """A single observable step within an agent run."""

    __tablename__ = "agent_run_steps"
    __table_args__ = (Index("ix_agent_run_steps_run_id_seq", "run_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_args_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    tool_result_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    similarity_to_prev: Mapped[float | None] = mapped_column(Float, nullable=True)
    stage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    truncated_payload_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
