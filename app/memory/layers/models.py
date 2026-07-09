"""
Memory Layers — SQLAlchemy ORM models and Pydantic schemas.

L1: MemoryAtomModel (structured facts extracted from conversations)
L2: MemorySceneModel (scene blocks aggregated from atoms)
L3: MemoryPersonaModel (user/agent persona synthesized from scenes)
Pipeline: MemoryPipelineStateModel (processing state per session)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base, TimestampMixin


# ============================================================
# Enums
# ============================================================


class AtomType(str, enum.Enum):
    """Type classification for memory atoms."""

    PERSONA = "persona"         # identity, preferences, style
    EPISODIC = "episodic"       # events, activities, experiences
    INSTRUCTION = "instruction"  # user rules, commands, corrections
    FACTUAL = "factual"         # facts, knowledge, data points


# ============================================================
# SQLAlchemy Models
# ============================================================


class MemoryAtomModel(Base, TimestampMixin):
    """L1: Structured memory atoms extracted from conversations."""

    __tablename__ = "memory_atoms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    atom_type: Mapped[AtomType] = mapped_column(
        SAEnum(AtomType, name="atom_type_enum"), nullable=False, default=AtomType.FACTUAL
    )
    importance: Mapped[float] = mapped_column(Float, default=0.5)  # 0.0 - 1.0
    embedding: Mapped[Optional[list]] = mapped_column(Vector(1536), nullable=True)
    source_message_ids: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MemorySceneModel(Base, TimestampMixin):
    """L2: Scene blocks aggregated from atoms."""

    __tablename__ = "memory_scenes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown content
    atom_ids: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # List of atom UUIDs
    atom_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class MemoryPersonaModel(Base, TimestampMixin):
    """L3: User/agent persona synthesized from scenes."""

    __tablename__ = "memory_personas"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)  # Markdown persona
    version: Mapped[int] = mapped_column(Integer, default=1)
    based_on_scene_ids: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    based_on_atom_count: Mapped[int] = mapped_column(Integer, default=0)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)


class MemoryPipelineStateModel(Base, TimestampMixin):
    """Pipeline state tracking per session."""

    __tablename__ = "memory_pipeline_state"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    agent_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    l1_cursor: Mapped[int] = mapped_column(Integer, default=0)  # messages processed by L1
    l2_cursor: Mapped[int] = mapped_column(Integer, default=0)  # atoms processed by L2
    l3_cursor: Mapped[int] = mapped_column(Integer, default=0)  # atoms count at last L3
    last_l1_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_l2_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_l3_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# ============================================================
# Pydantic Schemas
# ============================================================


class MemoryAtom(BaseModel):
    """Schema for creating a memory atom."""

    content: str
    atom_type: AtomType = AtomType.FACTUAL
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source_message_ids: Optional[list[str]] = None
    metadata: Optional[dict] = None


class MemoryAtomResponse(MemoryAtom):
    """Schema for memory atom API response."""

    id: str
    session_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


class RecallResult(BaseModel):
    """Single recall result from any layer."""

    content: str
    layer: str  # "l1", "l2", "l3"
    score: float
    atom_type: Optional[str] = None
    source: str = "unknown"  # "embedding", "keyword", "hybrid"


class RecallResponse(BaseModel):
    """Full recall response injected before a turn."""

    results: list[RecallResult] = []
    persona_snippet: Optional[str] = None
    total_tokens_used: int = 0
    strategy: str = "hybrid"
    duration_ms: int = 0


class L1ExtractionResult(BaseModel):
    """Result from L1 atom extraction."""

    atoms: list[MemoryAtom] = []
    messages_processed: int = 0


class PipelineState(BaseModel):
    """Schema for pipeline state tracking."""

    session_id: str
    message_count: int = 0
    l1_cursor: int = 0
    l2_cursor: int = 0
    l3_cursor: int = 0
    last_l1_at: Optional[datetime] = None
    last_l2_at: Optional[datetime] = None
    last_l3_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
