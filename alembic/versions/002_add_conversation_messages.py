"""Add conversation_messages table for session history & full-text search

Revision ID: 002
Revises: 001
Create Date: 2026-04-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("search_vector", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_conv_messages_session_id", "conversation_messages", ["session_id"]
    )
    op.create_index("ix_conv_messages_role", "conversation_messages", ["role"])
    op.create_index(
        "ix_conv_messages_created_at", "conversation_messages", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_conv_messages_created_at", table_name="conversation_messages")
    op.drop_index("ix_conv_messages_role", table_name="conversation_messages")
    op.drop_index("ix_conv_messages_session_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
