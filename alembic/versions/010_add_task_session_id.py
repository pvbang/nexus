"""Add session_id column to tasks table.

Revision ID: 010
Revises: 009
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add session_id column (nullable — existing tasks don't have it)
    op.add_column("tasks", sa.Column("session_id", sa.String(255), nullable=True))
    op.create_index("ix_tasks_session_id", "tasks", ["session_id"])

    # Backfill from metadata JSON where available
    op.execute("""
        UPDATE tasks
        SET session_id = COALESCE(
            metadata->>'session_id',
            metadata->>'conversation_id'
        )
        WHERE session_id IS NULL
          AND metadata IS NOT NULL
          AND (metadata->>'session_id' IS NOT NULL OR metadata->>'conversation_id' IS NOT NULL)
    """)


def downgrade() -> None:
    op.drop_index("ix_tasks_session_id", table_name="tasks")
    op.drop_column("tasks", "session_id")
