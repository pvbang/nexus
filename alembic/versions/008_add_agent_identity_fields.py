"""Add agent identity fields (avatar, expertise, thinking_style, background).

Revision ID: 008
Revises: 007_add_message_metadata
Create Date: 2026-04-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

# revision identifiers
revision = "008_add_agent_identity_fields"
down_revision = "007_add_message_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("avatar", sa.String(50), nullable=True))
    op.add_column("agents", sa.Column("expertise", JSON, nullable=True, server_default="[]"))
    op.add_column("agents", sa.Column("thinking_style", sa.String(100), nullable=True))
    op.add_column("agents", sa.Column("background", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "background")
    op.drop_column("agents", "thinking_style")
    op.drop_column("agents", "expertise")
    op.drop_column("agents", "avatar")
