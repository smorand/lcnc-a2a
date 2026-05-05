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
    op.add_column(
        "agents",
        sa.Column("provider_api_key_env_var", sa.String(length=120), nullable=True),
    )
    op.alter_column("agents", "provider_api_key_enc", nullable=True)


def downgrade() -> None:
    op.alter_column("agents", "provider_api_key_enc", nullable=False)
    op.drop_column("agents", "provider_api_key_env_var")
