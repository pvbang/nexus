"""
MekongAI Nexus - LightRAG (In-Memory Single-Document QA).

For quick single-document Q&A without persistent indexing.
Used when user uploads one file and asks questions immediately.

Usage::

    from app.rag.light_rag import LightRAG
    rag = LightRAG()
    await rag.ingest("document content...", source="report.pdf")
    chunks = await rag.query("What is the main finding?")
"""

from __future__ import annotations

from typing import Optional

import structlog

from app.rag.chunker import SmartChunker
from app.rag.models import Chunk, SearchResult

logger = structlog.get_logger(__name__)


class LightRAG:
    """In-memory single-document RAG for quick Q&A.

    Stores chunks and embeddings in memory (not persisted).
    Suitable for single-session, single-document use cases.
    """

    def __init__(
        self,
        chunk_size: int = 800,
        chunk_overlap: int = 150,
    ) -> None:
        self.chunker = SmartChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._chunks: list[Chunk] = []
        self._source: str = ""

    async def ingest(self, content: str, source: str = "") -> int:
        """Ingest a document into memory.

        Args:
            content: Full document text.
            source: Source identifier (filename).

        Returns:
            Number of chunks created.
        """
        self._source = source
        self._chunks = self.chunker.chunk_document(
            text=content,
            source=source,
            doc_id="light_rag",
        )

        # Embed chunks
        try:
            from app.xskill.embedding import generate_embedding

            for chunk in self._chunks:
                embedding = await generate_embedding(chunk.content[:8000])
                if embedding:
                    chunk.embedding = embedding
        except Exception as e:
            logger.warning("light_rag_embedding_failed", error=str(e))

        embedded = sum(1 for c in self._chunks if c.embedding is not None)
        logger.info(
            "light_rag_ingested",
            source=source,
            chunks=len(self._chunks),
            embedded=embedded,
        )
        return len(self._chunks)

    async def query(
        self,
        question: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Query the in-memory document.

        Uses cosine similarity if embeddings available, falls back to keyword matching.
        """
        if not self._chunks:
            return []

        # Try vector similarity
        query_embedding = await self._get_embedding(question)

        if query_embedding and any(c.embedding for c in self._chunks):
            return self._vector_search(query_embedding, top_k)
        else:
            return self._keyword_search(question, top_k)

    def clear(self) -> None:
        """Clear all ingested data."""
        self._chunks = []
        self._source = ""

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    @property
    def source(self) -> str:
        return self._source

    def _vector_search(
        self,
        query_embedding: list[float],
        top_k: int,
    ) -> list[SearchResult]:
        """Cosine similarity search in memory."""
        import math

        scored: list[tuple[float, Chunk]] = []
        for chunk in self._chunks:
            if chunk.embedding is None:
                continue
            sim = self._cosine_similarity(query_embedding, chunk.embedding)
            scored.append((sim, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            SearchResult(
                content=chunk.content,
                score=score,
                source=chunk.source,
                doc_id=chunk.doc_id,
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
                retrieval_method="light_rag_vector",
            )
            for score, chunk in scored[:top_k]
        ]

    def _keyword_search(
        self,
        query: str,
        top_k: int,
    ) -> list[SearchResult]:
        """Simple keyword frequency search as fallback."""
        query_words = set(query.lower().split())

        scored: list[tuple[float, Chunk]] = []
        for chunk in self._chunks:
            words = set(chunk.content.lower().split())
            overlap = len(query_words & words)
            if overlap > 0:
                score = overlap / max(len(query_words), 1)
                scored.append((score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            SearchResult(
                content=chunk.content,
                score=score,
                source=chunk.source,
                doc_id=chunk.doc_id,
                chunk_index=chunk.chunk_index,
                section_title=chunk.section_title,
                retrieval_method="light_rag_keyword",
            )
            for score, chunk in scored[:top_k]
        ]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    async def _get_embedding(self, text: str) -> Optional[list[float]]:
        try:
            from app.xskill.embedding import generate_embedding
            return await generate_embedding(text[:8000])
        except Exception:
            return None
