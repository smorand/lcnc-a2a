"""Agent model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lcnc_a2a.models.base import Base
from lcnc_a2a.models.types import PkUuid


class Agent(Base):
    """An A2A agent owned by a builder user."""

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_agents_user_name"),)

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
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_api_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    provider_api_key_env_var: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Up to 5 additional HTTP headers to send on every LLM call (encrypted
    # JSON map of name → value). Used by the "Other (OpenAI-compatible)"
    # preset for endpoints that need custom auth / org / project headers.
    provider_extra_headers_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    planner_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    executor_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    max_loops: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    similarity_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'stopped'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
