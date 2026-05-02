"""agent_mcp_servers, agent_contexts, agent_messages, agent_run_steps tables.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-01

Created here so US-003's cascade-delete tests can verify all dependent rows
disappear when an agent is deleted, without circular dependencies on the
later stories that populate them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "agent_mcp_servers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("transport", sa.String(length=20), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("env_enc", sa.LargeBinary(), nullable=True),
        sa.Column("cwd", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("headers_enc", sa.LargeBinary(), nullable=True),
        sa.Column(
            "tool_timeout_s",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("30"),
        ),
        sa.Column("tools_cache", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_mcp_servers_agent_id", "agent_mcp_servers", ["agent_id"])

    op.create_table(
        "agent_contexts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "message_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index("ix_agent_contexts_agent_id", "agent_contexts", ["agent_id"])

    op.create_table(
        "agent_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "context_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_contexts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_call_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_call_id", sa.String(length=100), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_agent_messages_context_id_position", "agent_messages", ["context_id", "position"])

    op.create_table(
        "agent_run_steps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("tool_name", sa.String(length=200), nullable=True),
        sa.Column("tool_args_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=True),
        sa.Column("tokens_out", sa.Integer(), nullable=True),
        sa.Column("similarity_to_prev", sa.Float(), nullable=True),
        sa.Column("stage", sa.Integer(), nullable=True),
        sa.Column("step_id", sa.Integer(), nullable=True),
        sa.Column("step_status", sa.String(length=20), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "truncated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("truncated_payload_sha256", sa.CHAR(length=64), nullable=True),
    )
    op.create_index("ix_agent_run_steps_run_id_seq", "agent_run_steps", ["run_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_agent_run_steps_run_id_seq", table_name="agent_run_steps")
    op.drop_table("agent_run_steps")
    op.drop_index("ix_agent_messages_context_id_position", table_name="agent_messages")
    op.drop_table("agent_messages")
    op.drop_index("ix_agent_contexts_agent_id", table_name="agent_contexts")
    op.drop_table("agent_contexts")
    op.drop_index("ix_agent_mcp_servers_agent_id", table_name="agent_mcp_servers")
    op.drop_table("agent_mcp_servers")
