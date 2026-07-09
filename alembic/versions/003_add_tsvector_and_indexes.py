"""Add TSVector column and GIN index for full-text search.

Revision ID: 003_add_tsvector_and_indexes
Revises: 002_add_conversation_messages
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '003_add_tsvector_and_indexes'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Change search_vector column to tsvector type
    # Use raw SQL because SQLAlchemy doesn't natively support tsvector
    op.execute("""
        ALTER TABLE conversation_messages 
        ALTER COLUMN search_vector TYPE tsvector 
        USING to_tsvector('english', COALESCE(content, ''))
    """)

    # Create GIN index for fast full-text search
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_conv_messages_search_vector
        ON conversation_messages USING gin(search_vector)
    """)

    # Create trigger to auto-update search_vector on INSERT/UPDATE
    op.execute("""
        CREATE OR REPLACE FUNCTION update_search_vector()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector('english', COALESCE(NEW.content, ''));
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_update_search_vector ON conversation_messages;
        CREATE TRIGGER trg_update_search_vector
        BEFORE INSERT OR UPDATE OF content ON conversation_messages
        FOR EACH ROW
        EXECUTE FUNCTION update_search_vector();
    """)

    # Add updated_at column if not exists (from DATA-03)
    op.execute("""
        DO $$ BEGIN
            ALTER TABLE conversation_messages 
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL;
        EXCEPTION WHEN duplicate_column THEN
            NULL;
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_update_search_vector ON conversation_messages")
    op.execute("DROP FUNCTION IF EXISTS update_search_vector()")
    op.execute("DROP INDEX IF EXISTS ix_conv_messages_search_vector")
    op.execute("""
        ALTER TABLE conversation_messages 
        ALTER COLUMN search_vector TYPE text
    """)
