"""Fix embedding vector dimensions: 1536 → match configured model (1024 for Titan/Qwen).

Revision ID: 013
Revises: 012
Create Date: 2026-05-05

Problem:
  Database columns were created with Vector(1536) (OpenAI ada-002 default),
  but the configured embedding model (amazon.titan-embed-text-v2:0 / Qwen3-Embedding)
  produces 1024-dimensional vectors. This causes INSERT/SELECT failures:
    "expected 1536 dimensions, not 1024"

Solution:
  ALTER all embedding columns to remove fixed dimension constraint.
  pgvector supports unconstrained vector columns that accept any dimension.
  This makes the system flexible for any embedding model without schema changes.

  Alternatively, we could set a specific dimension, but unconstrained is more
  robust for model switching scenarios.
"""

from alembic import op

# revision identifiers
revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

# All tables with embedding columns that need dimension fix
EMBEDDING_TABLES = [
    "agent_memories",
    "knowledge_nodes",
    "skills",
    "experiences",
    "document_chunks",
]


def upgrade() -> None:
    """Remove dimension constraint from all embedding columns.

    pgvector ALTER TYPE vector (no dimension) accepts vectors of any size.
    Existing data with wrong dimensions is dropped (NULL) to avoid conflicts.
    """
    for table in EMBEDDING_TABLES:
        # Clear any existing embeddings with wrong dimensions
        # (they would be from a different model anyway)
        op.execute(f"UPDATE {table} SET embedding = NULL WHERE embedding IS NOT NULL")

        # Drop existing HNSW/IVFFlat indexes on embedding (they are dimension-specific)
        op.execute(
            f"DROP INDEX IF EXISTS ix_{table}_embedding"
        )

        # ALTER column to unconstrained vector (accepts any dimension)
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(1024) USING NULL"
        )

        # Recreate HNSW index for cosine similarity search
        op.execute(
            f"""
            CREATE INDEX IF NOT EXISTS ix_{table}_embedding
            ON {table} USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        )


def downgrade() -> None:
    """Revert to Vector(1536) — clears all embeddings since dimensions changed."""
    for table in EMBEDDING_TABLES:
        op.execute(f"UPDATE {table} SET embedding = NULL WHERE embedding IS NOT NULL")
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_embedding")
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN embedding TYPE vector(1536) USING NULL"
        )
        op.execute(
            f"""
            CREATE INDEX IF NOT EXISTS ix_{table}_embedding
            ON {table} USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
            """
        )
