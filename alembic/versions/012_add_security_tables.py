"""Add security tables (approval_requests, tool_policies, security_audits).

Revision ID: 012
Revises: 011
Create Date: 2026-04-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Approval Requests ──
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tool_name", sa.String(128), nullable=False, index=True),
        sa.Column("tool_args", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("session_id", sa.String(64), nullable=False, index=True),
        sa.Column("user_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("channel", sa.String(32), nullable=False, server_default="web"),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        sa.Column("scope", sa.String(20), nullable=False, server_default="once"),
        sa.Column("decided_by", sa.String(128), nullable=True),
        sa.Column("deny_reason", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Index for cleanup of expired pending requests
    op.create_index(
        "ix_approval_requests_pending_created",
        "approval_requests",
        ["status", "created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ── Tool Policies ──
    op.create_table(
        "tool_policies",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("tool_pattern", sa.String(128), nullable=False, index=True),
        sa.Column(
            "action",
            sa.String(20),
            nullable=False,
            comment="allow, deny, ask, sandbox, allow_once",
        ),
        sa.Column("conditions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(128), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── Security Audit Results ──
    op.create_table(
        "security_audit_results",
        sa.Column(
            "id",
            sa.Integer(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("deep_mode", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("total_findings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("critical_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("medium_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("low_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("findings_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("checks_run", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checks_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("security_audit_results")
    op.drop_table("tool_policies")
    op.drop_index("ix_approval_requests_pending_created", table_name="approval_requests")
    op.drop_table("approval_requests")
