"""Add cascade delete constraints and fix ForeignKey relationships.

Revision ID: 004
Revises: 003_add_tsvector_and_indexes
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa

revision = '004_add_cascade_constraints'
down_revision = '003_add_tsvector_and_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add ON DELETE SET NULL for agents.swarm_id (when swarm is deleted, nullify agent's swarm_id)
    op.execute("""
        ALTER TABLE agents
        DROP CONSTRAINT IF EXISTS agents_swarm_id_fkey;
    """)
    op.execute("""
        ALTER TABLE agents
        ADD CONSTRAINT agents_swarm_id_fkey
        FOREIGN KEY (swarm_id) REFERENCES swarms(id)
        ON DELETE SET NULL;
    """)

    # Add ON DELETE CASCADE for tasks.parent_task_id
    op.execute("""
        ALTER TABLE tasks
        DROP CONSTRAINT IF EXISTS tasks_parent_task_id_fkey;
    """)
    op.execute("""
        ALTER TABLE tasks
        ADD CONSTRAINT tasks_parent_task_id_fkey
        FOREIGN KEY (parent_task_id) REFERENCES tasks(id)
        ON DELETE CASCADE;
    """)

    # Add ON DELETE CASCADE for agent_memories.agent_id
    op.execute("""
        ALTER TABLE agent_memories
        DROP CONSTRAINT IF EXISTS agent_memories_agent_id_fkey;
    """)
    op.execute("""
        ALTER TABLE agent_memories
        ADD CONSTRAINT agent_memories_agent_id_fkey
        FOREIGN KEY (agent_id) REFERENCES agents(id)
        ON DELETE CASCADE;
    """)

    # Add ON DELETE CASCADE for knowledge_edges
    op.execute("""
        ALTER TABLE knowledge_edges
        DROP CONSTRAINT IF EXISTS knowledge_edges_source_id_fkey;
    """)
    op.execute("""
        ALTER TABLE knowledge_edges
        ADD CONSTRAINT knowledge_edges_source_id_fkey
        FOREIGN KEY (source_id) REFERENCES knowledge_nodes(id)
        ON DELETE CASCADE;
    """)
    op.execute("""
        ALTER TABLE knowledge_edges
        DROP CONSTRAINT IF EXISTS knowledge_edges_target_id_fkey;
    """)
    op.execute("""
        ALTER TABLE knowledge_edges
        ADD CONSTRAINT knowledge_edges_target_id_fkey
        FOREIGN KEY (target_id) REFERENCES knowledge_nodes(id)
        ON DELETE CASCADE;
    """)


def downgrade() -> None:
    # Revert to default (NO ACTION) foreign keys
    for table, constraint, ref_table, column in [
        ("agents", "agents_swarm_id_fkey", "swarms", "swarm_id"),
        ("tasks", "tasks_parent_task_id_fkey", "tasks", "parent_task_id"),
        ("agent_memories", "agent_memories_agent_id_fkey", "agents", "agent_id"),
        ("knowledge_edges", "knowledge_edges_source_id_fkey", "knowledge_nodes", "source_id"),
        ("knowledge_edges", "knowledge_edges_target_id_fkey", "knowledge_nodes", "target_id"),
    ]:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}")
        op.execute(f"""
            ALTER TABLE {table}
            ADD CONSTRAINT {constraint}
            FOREIGN KEY ({column}) REFERENCES {ref_table}(id)
        """)
