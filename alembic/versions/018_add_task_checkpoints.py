"""Add task_checkpoints table for execution state persistence.

Revision ID: 018
Revises: 017
Create Date: 2026-05-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers
revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_checkpoints",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("state_json", JSONB(), nullable=False),
        sa.Column("reason", sa.String(50), nullable=False, server_default="turn_complete"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )
    op.create_index("ix_task_checkpoints_task_id", "task_checkpoints", ["task_id"])
    op.create_index("ix_task_checkpoints_created", "task_checkpoints", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_task_checkpoints_created")
    op.drop_index("ix_task_checkpoints_task_id")
    op.drop_table("task_checkpoints")
