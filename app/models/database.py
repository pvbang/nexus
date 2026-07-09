"""
MekongAI Nexus - SQLAlchemy Base & Database Models.

Định nghĩa tất cả ORM models cho hệ thống.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.xskill.embedding import get_embedding_dimensions


def _vector_type():
    """Get Vector column type with configured dimensions."""
    return Vector(get_embedding_dimensions())


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


class TimestampMixin:
    """Mixin thêm created_at, updated_at cho mọi model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ============================================================
# AGENT Models
# ============================================================


class AgentModel(Base, TimestampMixin):
    """Đại diện cho một AI Agent trong hệ thống."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False)  # researcher, developer, ...
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="idle"  # idle, active, busy, paused, terminated
    )
    personality: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skills: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    tools: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    config: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    llm_model: Mapped[str] = mapped_column(String(100), default="auto")

    # ── Agent Identity Fields (Phase 2) ──
    avatar: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # Emoji or icon identifier
    expertise: Mapped[Optional[list]] = mapped_column(JSON, default=list)  # List of expertise tags
    thinking_style: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # systematic, creative, analytical, critical, pragmatic, empirical, empathetic
    background: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Deep domain knowledge description

    parent_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    swarm_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # Relationships
    messages_sent = relationship("MessageModel", foreign_keys="MessageModel.sender_id", back_populates="sender")
    messages_received = relationship("MessageModel", foreign_keys="MessageModel.receiver_id", back_populates="receiver")
    memories = relationship("AgentMemoryModel", back_populates="agent",
                           cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        Index("ix_agents_status", "status"),
        Index("ix_agents_role", "role"),
        Index("ix_agents_swarm_id", "swarm_id"),
    )


# ============================================================
# TASK Models
# ============================================================


class TaskModel(Base, TimestampMixin):
    """Task được giao cho hệ thống xử lý."""

    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), default="pending"  # pending, decomposing, planning, executing, completed, failed, cancelled, crashed
    )
    priority: Mapped[int] = mapped_column(Integer, default=5)  # 1-10
    user_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    parent_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True
    )
    swarm_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)

    # ── Phase 1: Task Resilience - Heartbeat tracking ──
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_count: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    subtasks = relationship("TaskModel", back_populates="parent_task",
                           cascade="all, delete-orphan", passive_deletes=True)
    parent_task = relationship("TaskModel", back_populates="subtasks", remote_side="TaskModel.id")

    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_user_id", "user_id"),
        Index("ix_tasks_session_id", "session_id"),
        Index("ix_tasks_session_status", "session_id", "status"),
    )


class TaskEventModel(Base):
    """Persistent task execution events for replay and history.

    Stores every significant event during task execution (steps, tool calls,
    agent spawns, swarm rounds, etc.) so they survive server restart and
    can be replayed when the user reconnects.
    """

    __tablename__ = "task_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    event_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # step, tool_start, tool_done, agent_spawn, swarm_round, etc.
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # event payload
    sequence: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # Per-task ordering counter — ensures deterministic replay order
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_task_events_task_id", "task_id"),
        Index("ix_task_events_session_id", "session_id"),
        Index("ix_task_events_task_sequence", "task_id", "sequence"),
    )



# ============================================================
# COMMUNICATION Models
#
# NOTE: Two message storage systems coexist:
# 1. MessageModel — Agent-to-agent / swarm communication (internal)
# 2. ConversationMessageModel — User chat messages (session-indexed, FTS)
#
# User chat persistence uses ConversationMessageModel via session_search.
# MessageModel is for agent communication within swarms.
# ============================================================


class MessageModel(Base, TimestampMixin):
    """Agent-to-agent communication messages within swarms.

    ⚠️ DESIGN-1: This is for INTERNAL agent communication only (debate, vote, etc.)
    For USER chat messages (session history, FTS search), use ConversationMessageModel.
    Do NOT use this model to persist user ↔ assistant chat — it won't appear in
    session lists, search, or conversation history.
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    sender_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    receiver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    swarm_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    channel: Mapped[str] = mapped_column(
        String(50), default="direct"  # direct, broadcast, swarm, user
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(50), default="text"  # text, tool_call, tool_result, system, debate, vote
    )
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)

    # Relationships
    sender = relationship("AgentModel", foreign_keys=[sender_id], back_populates="messages_sent")
    receiver = relationship("AgentModel", foreign_keys=[receiver_id], back_populates="messages_received")

    __table_args__ = (
        Index("ix_messages_swarm_id", "swarm_id"),
        Index("ix_messages_sender_id", "sender_id"),
        Index("ix_messages_created_at", "created_at"),
    )


# ============================================================
# MEMORY Models
# ============================================================


class AgentMemoryModel(Base, TimestampMixin):
    """Ký ức dài hạn của Agent (vector-indexed)."""

    __tablename__ = "agent_memories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(
        String(50), default="episodic"  # episodic, semantic, procedural
    )
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    embedding: Mapped[Optional[list]] = mapped_column(_vector_type(), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)

    # Relationships
    agent = relationship("AgentModel", back_populates="memories")

    __table_args__ = (
        Index("ix_agent_memories_agent_id", "agent_id"),
        Index("ix_agent_memories_type", "memory_type"),
    )


class KnowledgeNodeModel(Base, TimestampMixin):
    """Node trong Knowledge Graph (GraphRAG)."""

    __tablename__ = "knowledge_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    properties: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    embedding: Mapped[Optional[list]] = mapped_column(_vector_type(), nullable=True)

    __table_args__ = (
        Index("ix_knowledge_nodes_type", "entity_type"),
        Index("ix_knowledge_nodes_name", "name"),
    )


class KnowledgeEdgeModel(Base, TimestampMixin):
    """Edge trong Knowledge Graph (GraphRAG)."""

    __tablename__ = "knowledge_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_nodes.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(200), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    properties: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_knowledge_edges_source", "source_id"),
        Index("ix_knowledge_edges_target", "target_id"),
    )


# ============================================================
# CONVERSATION / SESSION Models (Session Search - Hermes-inspired)
#
# Primary storage for user ↔ assistant chat messages.
# Indexed with PostgreSQL tsvector for full-text search.
# See also: alembic/versions/003_add_tsvector_and_indexes.py
# ============================================================


class ConversationMessageModel(Base):
    """Indexed conversation message for full-text search.

    Uses PostgreSQL tsvector for production FTS,
    falls back to LIKE search for dev/SQLite.
    """

    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(
        String(50), nullable=False  # user, assistant, system, tool
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSON, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    # Non-destructive compaction fields (Phase 2: Roo-Code pattern)
    # Messages are marked as compacted but never deleted, enabling rewind
    is_compacted: Mapped[bool] = mapped_column(
        Boolean, server_default="false", nullable=False
    )
    compaction_group_id: Mapped[Optional[str]] = mapped_column(
        String(36), nullable=True
    )

    # PostgreSQL tsvector column for full-text search
    # Uses TSVECTOR dialect type for proper INSERT/UPDATE compatibility.
    # Index created in alembic migration.
    search_vector = Column("search_vector", TSVECTOR, nullable=True)

    __table_args__ = (
        Index("ix_conv_messages_session_id", "session_id"),
        Index("ix_conv_messages_role", "role"),
        Index("ix_conv_messages_created_at", "created_at"),
        Index("ix_conv_messages_session_created", "session_id", "created_at"),
    )


# ============================================================
# TOOL / MCP Models
# ============================================================


# ============================================================
# XSKILL Models (Continual Learning)
# ============================================================


class SkillModel(Base, TimestampMixin):
    """Skill đã được đúc kết từ các task hoàn thành (task-level workflow)."""

    __tablename__ = "skills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(String(200), nullable=False)
    source_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True
    )
    tools_used: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    success_count: Mapped[int] = mapped_column(Integer, default=1)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    effectiveness_score: Mapped[float] = mapped_column(Float, default=0.5)
    embedding: Mapped[Optional[list]] = mapped_column(_vector_type(), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("ix_skills_task_type", "task_type"),
        Index("ix_skills_effectiveness", "effectiveness_score"),
    )


class ExperienceModel(Base, TimestampMixin):
    """Experience đúc kết từ hành động - bài học chiến thuật (action-level)."""

    __tablename__ = "experiences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    experience_type: Mapped[str] = mapped_column(
        String(50), default="tactical"  # tactical, error_recovery, optimization, pattern
    )
    source_task_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id"), nullable=True
    )
    source_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True
    )
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    lesson: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    times_applied: Mapped[int] = mapped_column(Integer, default=0)
    times_helpful: Mapped[int] = mapped_column(Integer, default=0)
    embedding: Mapped[Optional[list]] = mapped_column(_vector_type(), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("ix_experiences_type", "experience_type"),
        Index("ix_experiences_importance", "importance"),
    )


class ToolModel(Base, TimestampMixin):
    """MCP Tool đã đăng ký trong hệ thống."""

    __tablename__ = "tools"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tool_type: Mapped[str] = mapped_column(
        String(50), default="mcp"  # mcp, builtin, dynamic, wasm
    )
    schema_: Mapped[dict] = mapped_column("schema", JSON, nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sandbox_required: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_tools_type", "tool_type"),)


# ============================================================
# RAG Document Chunks (pgvector-indexed)
# ============================================================

class DocumentChunkModel(Base, TimestampMixin):
    """RAG document chunk with pgvector embedding for similarity search."""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    collection: Mapped[str] = mapped_column(String(100), default="documents", index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    section_title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)
    embedding: Mapped[Optional[list]] = mapped_column(_vector_type(), nullable=True)

    __table_args__ = (
        Index("ix_doc_chunks_doc_collection", "doc_id", "collection"),
        Index("ix_doc_chunks_collection", "collection"),
    )


class IndexedDocumentModel(Base, TimestampMixin):
    """Metadata for an indexed document in the RAG system."""

    __tablename__ = "indexed_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    doc_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    total_chars: Mapped[int] = mapped_column(Integer, default=0)
    collection: Mapped[str] = mapped_column(String(100), default="documents")
    status: Mapped[str] = mapped_column(String(50), default="indexed")  # indexed | failed | deleted
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("ix_indexed_docs_session", "session_id"),
        Index("ix_indexed_docs_status", "status"),
    )


# ============================================================
# TASK CHECKPOINT Models
# ============================================================


class TaskCheckpointModel(Base):
    """Persistent checkpoint for task execution state (resume after crash/restart)."""

    __tablename__ = "task_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(
        String(50), nullable=False, default="turn_complete"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_task_checkpoints_task_id", "task_id"),
        Index("ix_task_checkpoints_created", "created_at"),
    )
