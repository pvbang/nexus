"""Add compaction tracking to conversation messages.

Revision ID: 014_add_compaction_fields
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    """Add non-destructive compaction fields to conversation_messages."""
    op.add_column(
        "conversation_messages",
        sa.Column("is_compacted", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "conversation_messages",
        sa.Column("compaction_group_id", sa.String(36), nullable=True),
    )
    # Index for efficient filtering of visible (non-compacted) messages
    op.create_index(
        "ix_conv_messages_compacted",
        "conversation_messages",
        ["session_id", "is_compacted"],
    )


def downgrade():
    op.drop_index("ix_conv_messages_compacted", table_name="conversation_messages")
    op.drop_column("conversation_messages", "compaction_group_id")
    op.drop_column("conversation_messages", "is_compacted")
