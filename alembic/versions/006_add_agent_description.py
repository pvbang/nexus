"""Add description field to agents table.

Revision ID: 006_add_agent_description
Revises: 005_add_task_events
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa

revision = "006_add_agent_description"
down_revision = "005_add_task_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "description")
