"""Add RAG document_chunks and indexed_documents tables.

Revision ID: 009
Revises: 008_add_agent_identity_fields
Create Date: 2026-04-23
"""

from alembic import op

revision = "009"
down_revision = "008_add_agent_identity_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure pgvector extension exists
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Document chunks table with pgvector embedding
    op.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id VARCHAR(64) PRIMARY KEY,
            doc_id VARCHAR(255) NOT NULL,
            collection VARCHAR(100) DEFAULT 'documents',
            chunk_index INTEGER DEFAULT 0,
            total_chunks INTEGER DEFAULT 0,
            content TEXT NOT NULL,
            source VARCHAR(500),
            section_title VARCHAR(500),
            char_start INTEGER DEFAULT 0,
            char_end INTEGER DEFAULT 0,
            metadata JSONB DEFAULT '{}',
            embedding vector(1536),
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_doc_chunks_doc_collection ON document_chunks(doc_id, collection)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_doc_chunks_collection ON document_chunks(collection)")
    # HNSW index for fast approximate nearest neighbor search
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_doc_chunks_embedding ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
    """)

    # Indexed documents metadata table
    op.execute("""
        CREATE TABLE IF NOT EXISTS indexed_documents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id VARCHAR(255),
            doc_id VARCHAR(255) NOT NULL UNIQUE,
            filename VARCHAR(500) NOT NULL,
            original_path TEXT,
            file_size INTEGER DEFAULT 0,
            mime_type VARCHAR(100),
            chunk_count INTEGER DEFAULT 0,
            total_chars INTEGER DEFAULT 0,
            collection VARCHAR(100) DEFAULT 'documents',
            status VARCHAR(50) DEFAULT 'indexed',
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS ix_indexed_docs_session ON indexed_documents(session_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_indexed_docs_status ON indexed_documents(status)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS indexed_documents")
    op.execute("DROP TABLE IF EXISTS document_chunks")
