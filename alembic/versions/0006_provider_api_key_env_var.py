"""provider_api_key_env_var column for runtime env-var resolution.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Batch mode is required for ``alter_column`` on SQLite (which has no
    # ``ALTER COLUMN``); on PostgreSQL it falls back to a plain ``ALTER``.
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column("provider_api_key_env_var", sa.String(length=120), nullable=True),
        )
        batch_op.alter_column("provider_api_key_enc", nullable=True, existing_type=sa.LargeBinary())


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.alter_column("provider_api_key_enc", nullable=False, existing_type=sa.LargeBinary())
        batch_op.drop_column("provider_api_key_env_var")
