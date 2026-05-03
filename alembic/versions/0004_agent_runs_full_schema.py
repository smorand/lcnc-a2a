"""Extend agent_runs to its full US-005 schema.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "context_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_contexts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("agent_runs", sa.Column("a2a_task_id", sa.String(length=100), nullable=True))
    op.add_column("agent_runs", sa.Column("stop_reason", sa.String(length=60), nullable=True))
    op.add_column("agent_runs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agent_runs", sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("agent_runs", sa.Column("final_answer", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("config_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "config_snapshot")
    op.drop_column("agent_runs", "final_answer")
    op.drop_column("agent_runs", "plan")
    op.drop_column("agent_runs", "completed_at")
    op.drop_column("agent_runs", "stop_reason")
    op.drop_column("agent_runs", "a2a_task_id")
    op.drop_column("agent_runs", "context_id")
