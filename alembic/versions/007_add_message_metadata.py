"""Add metadata column to conversation_messages

Revision ID: 007_add_message_metadata
Revises: 006_add_agent_description
Create Date: 2026-04-22
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "007_add_message_metadata"
down_revision = "006_add_agent_description"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("metadata", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "metadata")
