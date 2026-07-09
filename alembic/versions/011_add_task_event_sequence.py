"""Add sequence column to task_events table.

Provides deterministic ordering for events within a task,
independent of created_at timestamp (which can have collisions
for events emitted in the same millisecond).

Revision ID: 011
Revises: 010
Create Date: 2026-04-24
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sequence column (nullable — existing events don't have it)
    op.add_column(
        "task_events",
        sa.Column("sequence", sa.Integer(), nullable=True),
    )
    # Composite index for fast ordered retrieval: ORDER BY task_id, sequence
    op.create_index(
        "ix_task_events_task_sequence",
        "task_events",
        ["task_id", "sequence"],
    )

    # Backfill existing rows with sequential numbers per task
    # Uses row_number() over created_at ordering within each task
    op.execute("""
        UPDATE task_events AS te
        SET sequence = sub.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY task_id ORDER BY created_at, id
            ) - 1 AS rn
            FROM task_events
        ) AS sub
        WHERE te.id = sub.id
          AND te.sequence IS NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_task_events_task_sequence", table_name="task_events")
    op.drop_column("task_events", "sequence")
