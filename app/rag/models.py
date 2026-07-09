"""
MekongAI Nexus - RAG Data Models.

Dataclasses for chunks, search results, and index metadata.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class Chunk:
    """A single chunk of a document."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    content: str = ""
    doc_id: str = ""
    chunk_index: int = 0
    total_chunks: int = 0
    source: str = ""
    section_title: str = ""
    char_start: int = 0
    char_end: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: Optional[list[float]] = None


@dataclass
class SearchResult:
    """A single result from vector/hybrid search."""

    content: str = ""
    score: float = 0.0
    source: str = ""
    doc_id: str = ""
    chunk_index: int = 0
    section_title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    retrieval_method: str = "vector"  # vector | graph | fts


@dataclass
class IndexResult:
    """Result of indexing a document."""

    doc_id: str = ""
    filename: str = ""
    chunk_count: int = 0
    total_chars: int = 0
    status: str = "indexed"  # indexed | failed | skipped
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class IndexedDocument:
    """Metadata for an indexed document."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    filename: str = ""
    original_path: str = ""
    file_size: int = 0
    mime_type: str = ""
    chunk_count: int = 0
    total_chars: int = 0
    collection: str = "documents"
    status: str = "indexed"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GraphChunk:
    """A chunk retrieved via knowledge graph traversal."""

    content: str = ""
    entity: str = ""
    entity_type: str = ""
    relation: str = ""
    related_entity: str = ""
    depth: int = 0
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
