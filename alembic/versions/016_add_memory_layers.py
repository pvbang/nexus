"""Add memory layers tables (L1 atoms, L2 scenes, L3 personas, pipeline state).

Revision ID: 016
Revises: 015
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

# revision identifiers
revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # L1: Memory Atoms
    op.create_table(
        "memory_atoms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("agent_id", sa.String(100), nullable=True),
        sa.Column("user_id", sa.String(100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("atom_type", sa.String(20), nullable=False, server_default="factual"),
        sa.Column("importance", sa.Float(), server_default="0.5"),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("source_message_ids", sa.JSON(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_atoms_session_id", "memory_atoms", ["session_id"])
    op.create_index("ix_memory_atoms_agent_id", "memory_atoms", ["agent_id"])
    op.create_index("ix_memory_atoms_user_id", "memory_atoms", ["user_id"])
    op.create_index("ix_memory_atoms_type_importance", "memory_atoms", ["atom_type", "importance"])
    op.create_index("ix_memory_atoms_user_agent", "memory_atoms", ["user_id", "agent_id"])

    # Add tsvector column for FTS
    op.execute("""
        ALTER TABLE memory_atoms
        ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    """)
    op.create_index(
        "ix_memory_atoms_search_vector", "memory_atoms", ["search_vector"],
        postgresql_using="gin"
    )
    op.create_index(
        "ix_memory_atoms_embedding", "memory_atoms", ["embedding"],
        postgresql_using="ivfflat",
        postgresql_ops={"embedding": "vector_cosine_ops"}
    )

    # L2: Memory Scenes
    op.create_table(
        "memory_scenes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", sa.String(100), nullable=True),
        sa.Column("user_id", sa.String(100), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("atom_ids", sa.JSON(), nullable=True),
        sa.Column("atom_count", sa.Integer(), server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_scenes_agent_id", "memory_scenes", ["agent_id"])
    op.create_index("ix_memory_scenes_user_id", "memory_scenes", ["user_id"])
    op.create_index("ix_memory_scenes_user_agent", "memory_scenes", ["user_id", "agent_id"])

    # L3: Memory Personas
    op.create_table(
        "memory_personas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", sa.String(100), nullable=True),
        sa.Column("user_id", sa.String(100), nullable=True),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1"),
        sa.Column("based_on_scene_ids", sa.JSON(), nullable=True),
        sa.Column("based_on_atom_count", sa.Integer(), server_default="0"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_personas_agent_id", "memory_personas", ["agent_id"])
    op.create_index("ix_memory_personas_user_id", "memory_personas", ["user_id"])
    op.create_index("ix_memory_personas_user_agent", "memory_personas", ["user_id", "agent_id"])

    # Pipeline State
    op.create_table(
        "memory_pipeline_state",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", sa.String(100), nullable=False, unique=True),
        sa.Column("agent_id", sa.String(100), nullable=True),
        sa.Column("user_id", sa.String(100), nullable=True),
        sa.Column("message_count", sa.Integer(), server_default="0"),
        sa.Column("l1_cursor", sa.Integer(), server_default="0"),
        sa.Column("l2_cursor", sa.Integer(), server_default="0"),
        sa.Column("l3_cursor", sa.Integer(), server_default="0"),
        sa.Column("last_l1_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_l2_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_l3_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_memory_pipeline_state_session_id", "memory_pipeline_state", ["session_id"])


def downgrade() -> None:
    op.drop_table("memory_pipeline_state")
    op.drop_table("memory_personas")
    op.drop_table("memory_scenes")
    op.drop_index("ix_memory_atoms_search_vector", table_name="memory_atoms")
    op.drop_index("ix_memory_atoms_embedding", table_name="memory_atoms")
    op.drop_index("ix_memory_atoms_type_importance", table_name="memory_atoms")
    op.drop_index("ix_memory_atoms_user_agent", table_name="memory_atoms")
    op.drop_table("memory_atoms")
