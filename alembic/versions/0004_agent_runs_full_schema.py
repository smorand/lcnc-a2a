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
    # ``batch_alter_table`` is a no-op on PostgreSQL but rewrites the table
    # in copy-and-move style on SQLite, which is the only way to add a
    # column with a FOREIGN KEY constraint there.
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "context_id",
                sa.Uuid(as_uuid=True),
                sa.ForeignKey(
                    "agent_contexts.id",
                    name="fk_agent_runs_context_id",
                    ondelete="SET NULL",
                ),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("a2a_task_id", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("stop_reason", sa.String(length=60), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column(
                "plan",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("final_answer", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "config_snapshot",
                sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_column("config_snapshot")
        batch_op.drop_column("final_answer")
        batch_op.drop_column("plan")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("stop_reason")
        batch_op.drop_column("a2a_task_id")
        batch_op.drop_column("context_id")
