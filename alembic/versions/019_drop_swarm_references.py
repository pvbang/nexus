"""Drop swarm foreign key constraints and swarms table.

Revision ID: 019
Revises: 018
Create Date: 2026-06-02
"""

from alembic import op

# revision identifiers
revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop FK constraints referencing swarms table
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_swarm_id_fkey")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_swarm_id_fkey")
    op.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_swarm_id_fkey")

    # Drop the swarms table
    op.execute("DROP TABLE IF EXISTS swarms")


def downgrade() -> None:
    # Recreate swarms table
    op.execute("""
        CREATE TABLE IF NOT EXISTS swarms (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            strategy VARCHAR(50) DEFAULT 'debate',
            status VARCHAR(50) DEFAULT 'idle',
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Re-add FK constraints
    op.execute("""
        ALTER TABLE agents
        ADD CONSTRAINT agents_swarm_id_fkey
        FOREIGN KEY (swarm_id) REFERENCES swarms(id)
        ON DELETE SET NULL
    """)
    op.execute("""
        ALTER TABLE tasks
        ADD CONSTRAINT tasks_swarm_id_fkey
        FOREIGN KEY (swarm_id) REFERENCES swarms(id)
    """)
    op.execute("""
        ALTER TABLE messages
        ADD CONSTRAINT messages_swarm_id_fkey
        FOREIGN KEY (swarm_id) REFERENCES swarms(id)
    """)
