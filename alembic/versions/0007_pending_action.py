"""Add agent_runs.pending_action JSONB for paused (input-required) runs.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("pending_action", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "pending_action")
