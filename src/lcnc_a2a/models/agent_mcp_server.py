"""AgentMcpServer model (skeletal; full behavior lands in US-004)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lcnc_a2a.models.base import Base
from lcnc_a2a.models.types import JsonField, PkUuid


class AgentMcpServer(Base):
    """An MCP server attached to an agent. Schema only in US-003."""

    __tablename__ = "agent_mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        primary_key=True,
        default=uuid.uuid4,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PkUuid(),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    transport: Mapped[str] = mapped_column(String(20), nullable=False)
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    env_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    headers_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    tool_timeout_s: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("30"))
    tools_cache: Mapped[Any | None] = mapped_column(JsonField(), nullable=True)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
