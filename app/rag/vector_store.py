"""
MekongAI Nexus - PostgreSQL pgvector Vector Store.

Stores and retrieves document chunks using PostgreSQL with pgvector extension.
Uses the existing database connection pool and embedding service.

Usage::

    from app.rag.vector_store import PgVectorStore
    store = PgVectorStore(db_session)
    await store.add_chunks(chunks)
    results = await store.search("query text", top_k=5)
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

import structlog
from sqlalchemy import text, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.models import Chunk, SearchResult
from app.xskill.embedding import get_embedding_dimensions

logger = structlog.get_logger(__name__)

# Embedding dimensions — resolved from the configured embedding model (env
# override → model lookup → fallback), the SAME source the SQLAlchemy models and
# Alembic migrations use, so RAG vectors never mismatch the DB column. Previously
# hardcoded to 1536 (OpenAI), which silently disagreed with Gemini (768) /
# Titan (1024) setups.
EMBEDDING_DIM = get_embedding_dimensions()


class PgVectorStore:
    """PostgreSQL pgvector-based vector store for RAG document chunks."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add_chunks(
        self,
        chunks: list[Chunk],
        collection: str = "documents",
    ) -> list[str]:
        """Store chunks with embeddings into PostgreSQL.

        Args:
            chunks: List of Chunk objects (must have embedding set).
            collection: Collection/namespace name.

        Returns:
            List of stored chunk IDs.
        """
        if not chunks:
            return []

        stored_ids: list[str] = []
        for chunk in chunks:
            chunk_id = chunk.id or uuid.uuid4().hex[:16]

            await self.db.execute(
                text("""
                    INSERT INTO document_chunks
                        (id, doc_id, collection, chunk_index, total_chunks,
                         content, source, section_title, char_start, char_end,
                         metadata, embedding)
                    VALUES
                        (:id, :doc_id, :collection, :chunk_index, :total_chunks,
                         :content, :source, :section_title, :char_start, :char_end,
                         :metadata::jsonb, :embedding::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        content = EXCLUDED.content,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata
                """),
                {
                    "id": chunk_id,
                    "doc_id": chunk.doc_id,
                    "collection": collection,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                    "content": chunk.content,
                    "source": chunk.source,
                    "section_title": chunk.section_title,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "metadata": _json_dumps(chunk.metadata),
                    "embedding": _format_vector(chunk.embedding),
                },
            )
            stored_ids.append(chunk_id)

        await self.db.commit()
        logger.info(
            "chunks_stored",
            count=len(stored_ids),
            collection=collection,
            doc_id=chunks[0].doc_id if chunks else "",
        )
        return stored_ids

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        collection: str = "documents",
        doc_id: Optional[str] = None,
        score_threshold: float = 0.0,
    ) -> list[SearchResult]:
        """Vector similarity search using cosine distance.

        Args:
            query_embedding: Query embedding vector.
            top_k: Max results to return.
            collection: Filter by collection name.
            doc_id: Optional filter by document ID.
            score_threshold: Min similarity score (0-1, higher=more similar).

        Returns:
            List of SearchResult ordered by similarity (highest first).
        """
        filters = ["collection = :collection", "embedding IS NOT NULL"]
        params: dict[str, Any] = {
            "collection": collection,
            "embedding": _format_vector(query_embedding),
            "limit": top_k,
        }

        if doc_id:
            filters.append("doc_id = :doc_id")
            params["doc_id"] = doc_id

        where_clause = " AND ".join(filters)

        result = await self.db.execute(
            text(f"""
                SELECT id, doc_id, content, source, section_title,
                       chunk_index, metadata,
                       1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                FROM document_chunks
                WHERE {where_clause}
                ORDER BY embedding <=> CAST(:embedding AS vector) ASC
                LIMIT :limit
            """),
            params,
        )

        rows = result.fetchall()
        results: list[SearchResult] = []

        for row in rows:
            sim = float(row.similarity) if row.similarity else 0.0
            if sim < score_threshold:
                continue
            results.append(SearchResult(
                content=row.content,
                score=sim,
                source=row.source or "",
                doc_id=row.doc_id or "",
                chunk_index=row.chunk_index or 0,
                section_title=row.section_title or "",
                metadata=_parse_json(row.metadata),
                retrieval_method="vector",
            ))

        return results

    async def delete_by_doc_id(
        self,
        doc_id: str,
        collection: str = "documents",
    ) -> int:
        """Delete all chunks for a document.

        Returns:
            Number of chunks deleted.
        """
        result = await self.db.execute(
            text("""
                DELETE FROM document_chunks
                WHERE doc_id = :doc_id AND collection = :collection
            """),
            {"doc_id": doc_id, "collection": collection},
        )
        await self.db.commit()
        deleted = result.rowcount or 0
        logger.info("chunks_deleted", doc_id=doc_id, count=deleted)
        return deleted

    async def get_doc_chunks(
        self,
        doc_id: str,
        collection: str = "documents",
    ) -> list[Chunk]:
        """Get all chunks for a document, ordered by chunk_index."""
        result = await self.db.execute(
            text("""
                SELECT id, doc_id, content, source, section_title,
                       chunk_index, total_chunks, char_start, char_end, metadata
                FROM document_chunks
                WHERE doc_id = :doc_id AND collection = :collection
                ORDER BY chunk_index ASC
            """),
            {"doc_id": doc_id, "collection": collection},
        )

        return [
            Chunk(
                id=row.id,
                doc_id=row.doc_id,
                content=row.content,
                source=row.source or "",
                section_title=row.section_title or "",
                chunk_index=row.chunk_index or 0,
                total_chunks=row.total_chunks or 0,
                char_start=row.char_start or 0,
                char_end=row.char_end or 0,
                metadata=_parse_json(row.metadata),
            )
            for row in result.fetchall()
        ]

    async def collection_stats(self, collection: str = "documents") -> dict[str, Any]:
        """Get stats for a collection."""
        result = await self.db.execute(
            text("""
                SELECT
                    COUNT(*) AS chunk_count,
                    COUNT(DISTINCT doc_id) AS doc_count,
                    SUM(LENGTH(content)) AS total_chars,
                    COUNT(CASE WHEN embedding IS NOT NULL THEN 1 END) AS embedded_count
                FROM document_chunks
                WHERE collection = :collection
            """),
            {"collection": collection},
        )
        row = result.fetchone()
        if not row:
            return {"chunk_count": 0, "doc_count": 0, "total_chars": 0, "embedded_count": 0}

        return {
            "chunk_count": row.chunk_count or 0,
            "doc_count": row.doc_count or 0,
            "total_chars": row.total_chars or 0,
            "embedded_count": row.embedded_count or 0,
        }

    async def fts_search(
        self,
        query: str,
        top_k: int = 5,
        collection: str = "documents",
        doc_id: Optional[str] = None,
    ) -> list[SearchResult]:
        """Full-text search using PostgreSQL tsvector.

        Complement to vector search for exact keyword matches.
        """
        filters = ["collection = :collection"]
        params: dict[str, Any] = {
            "collection": collection,
            "query": query,
            "limit": top_k,
        }

        if doc_id:
            filters.append("doc_id = :doc_id")
            params["doc_id"] = doc_id

        where_clause = " AND ".join(filters)

        result = await self.db.execute(
            text(f"""
                SELECT id, doc_id, content, source, section_title,
                       chunk_index, metadata,
                       ts_rank(
                           to_tsvector('english', content),
                           plainto_tsquery('english', :query)
                       ) AS rank
                FROM document_chunks
                WHERE {where_clause}
                  AND to_tsvector('english', content) @@ plainto_tsquery('english', :query)
                ORDER BY rank DESC
                LIMIT :limit
            """),
            params,
        )

        return [
            SearchResult(
                content=row.content,
                score=float(row.rank) if row.rank else 0.0,
                source=row.source or "",
                doc_id=row.doc_id or "",
                chunk_index=row.chunk_index or 0,
                section_title=row.section_title or "",
                metadata=_parse_json(row.metadata),
                retrieval_method="fts",
            )
            for row in result.fetchall()
        ]


# ── Helpers ────────────────────────────────────────────────────

def _format_vector(vec: Optional[list[float]]) -> Optional[str]:
    """Format a list of floats as a pgvector string '[1.0,2.0,...]'."""
    if vec is None:
        return None
    return "[" + ",".join(str(v) for v in vec) + "]"


def _json_dumps(obj: Any) -> str:
    """Safely dump a dict to JSON string."""
    import json
    if isinstance(obj, str):
        return obj
    return json.dumps(obj or {}, ensure_ascii=False)


def _parse_json(val: Any) -> dict[str, Any]:
    """Parse a JSON value from DB."""
    import json
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}
