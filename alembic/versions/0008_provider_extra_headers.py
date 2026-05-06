"""Add agents.provider_extra_headers_enc for custom HTTP headers on the LLM call.

When the model is hosted behind an OpenAI-compatible endpoint that needs
extra headers (custom auth, org ID, project token, ...), the agent owner
can configure up to 5 additional headers. They are stored encrypted as a
JSON map and merged into every ``_post_chat`` request.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("provider_extra_headers_enc", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "provider_extra_headers_enc")
