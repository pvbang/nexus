"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSON
from pgvector.sqlalchemy import Vector

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Swarms table (referenced by agents)
    op.create_table(
        "swarms",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(50), default="forming"),
        sa.Column("strategy", sa.String(100), default="debate"),
        sa.Column("config", JSON, default={}),
        sa.Column("result", JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_swarms_status", "swarms", ["status"])

    # Tasks table
    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("priority", sa.Integer, default=5),
        sa.Column("user_id", sa.String(255), nullable=True),
        sa.Column("parent_task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("swarm_id", UUID(as_uuid=True), sa.ForeignKey("swarms.id"), nullable=True),
        sa.Column("result", JSON, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("metadata", JSON, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])

    # Add FK from swarms to tasks
    op.create_foreign_key("fk_swarms_task_id", "swarms", "tasks", ["task_id"], ["id"])

    # Agents table
    op.create_table(
        "agents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), default="idle"),
        sa.Column("personality", sa.Text, nullable=True),
        sa.Column("system_prompt", sa.Text, nullable=True),
        sa.Column("skills", JSON, default={}),
        sa.Column("tools", JSON, default=[]),
        sa.Column("config", JSON, default={}),
        sa.Column("llm_model", sa.String(100), default="gpt-4o-mini"),
        sa.Column("parent_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("swarm_id", UUID(as_uuid=True), sa.ForeignKey("swarms.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agents_status", "agents", ["status"])
    op.create_index("ix_agents_role", "agents", ["role"])
    op.create_index("ix_agents_swarm_id", "agents", ["swarm_id"])

    # Messages table
    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sender_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("receiver_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("swarm_id", UUID(as_uuid=True), sa.ForeignKey("swarms.id"), nullable=True),
        sa.Column("channel", sa.String(50), default="direct"),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("message_type", sa.String(50), default="text"),
        sa.Column("metadata", JSON, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_messages_swarm_id", "messages", ["swarm_id"])
    op.create_index("ix_messages_sender_id", "messages", ["sender_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])

    # Agent Memories table (with vector)
    op.create_table(
        "agent_memories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("memory_type", sa.String(50), default="episodic"),
        sa.Column("importance", sa.Float, default=0.5),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("metadata", JSON, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_memories_agent_id", "agent_memories", ["agent_id"])
    op.create_index("ix_agent_memories_type", "agent_memories", ["memory_type"])

    # Knowledge Nodes
    op.create_table(
        "knowledge_nodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("properties", JSON, default={}),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_knowledge_nodes_type", "knowledge_nodes", ["entity_type"])
    op.create_index("ix_knowledge_nodes_name", "knowledge_nodes", ["name"])

    # Knowledge Edges
    op.create_table(
        "knowledge_edges",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey("knowledge_nodes.id"), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), sa.ForeignKey("knowledge_nodes.id"), nullable=False),
        sa.Column("relation_type", sa.String(200), nullable=False),
        sa.Column("weight", sa.Float, default=1.0),
        sa.Column("properties", JSON, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_knowledge_edges_source", "knowledge_edges", ["source_id"])
    op.create_index("ix_knowledge_edges_target", "knowledge_edges", ["target_id"])

    # Tools table
    op.create_table(
        "tools",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("tool_type", sa.String(50), default="mcp"),
        sa.Column("schema", JSON, nullable=False),
        sa.Column("config", JSON, default={}),
        sa.Column("is_active", sa.Boolean, default=True),
        sa.Column("sandbox_required", sa.Boolean, default=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tools_type", "tools", ["tool_type"])

    # XSkill: Skills table
    op.create_table(
        "skills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("task_type", sa.String(200), nullable=False),
        sa.Column("source_task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("tools_used", JSON, default=[]),
        sa.Column("success_count", sa.Integer, default=1),
        sa.Column("fail_count", sa.Integer, default=0),
        sa.Column("effectiveness_score", sa.Float, default=0.5),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("metadata", JSON, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_skills_task_type", "skills", ["task_type"])
    op.create_index("ix_skills_effectiveness", "skills", ["effectiveness_score"])

    # XSkill: Experiences table
    op.create_table(
        "experiences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("experience_type", sa.String(50), default="tactical"),
        sa.Column("source_task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("source_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("context", sa.Text, nullable=True),
        sa.Column("lesson", sa.Text, nullable=True),
        sa.Column("importance", sa.Float, default=0.5),
        sa.Column("times_applied", sa.Integer, default=0),
        sa.Column("times_helpful", sa.Integer, default=0),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("metadata", JSON, default={}),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_experiences_type", "experiences", ["experience_type"])
    op.create_index("ix_experiences_importance", "experiences", ["importance"])


def downgrade() -> None:
    op.drop_table("experiences")
    op.drop_table("skills")
    op.drop_table("tools")
    op.drop_table("knowledge_edges")
    op.drop_table("knowledge_nodes")
    op.drop_table("agent_memories")
    op.drop_table("messages")
    op.drop_table("agents")
    op.drop_table("tasks")
    op.drop_table("swarms")
    op.execute("DROP EXTENSION IF EXISTS vector")
