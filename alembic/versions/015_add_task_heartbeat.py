"""Add task heartbeat columns for resilience monitoring.

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tasks", sa.Column("heartbeat_count", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("tasks", "heartbeat_count")
    op.drop_column("tasks", "last_heartbeat")
