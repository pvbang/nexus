"""Widen agent text columns from VARCHAR(100) to Text.

Revision ID: 017
Revises: 016
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # thinking_style was VARCHAR(100) — too restrictive for some agent descriptions
    op.alter_column(
        "agents",
        "thinking_style",
        existing_type=sa.String(100),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "agents",
        "thinking_style",
        existing_type=sa.Text(),
        type_=sa.String(100),
        existing_nullable=True,
    )
