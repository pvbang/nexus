"""
MekongAI Nexus - Hybrid Retriever.

Combines vector search + FTS + graph traversal using
Reciprocal Rank Fusion (RRF) for robust retrieval.

Usage::

    from app.rag.retriever import HybridRetriever
    retriever = HybridRetriever(db_session)
    results = await retriever.retrieve("What is FastAPI?", top_k=5)
"""

from __future__ import annotations

from typing import Any, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.rag.models import SearchResult
from app.rag.vector_store import PgVectorStore

logger = structlog.get_logger(__name__)

# RRF constant (standard value from literature)
RRF_K = 60


class HybridRetriever:
    """Hybrid retrieval combining vector, FTS, and graph search with RRF fusion."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings()
        self.store = PgVectorStore(db)

    async def retrieve(
        self,
        query: str,
        top_k: int = 0,
        collection: str = "",
        doc_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[SearchResult]:
        """Run hybrid retrieval: vector + FTS + graph, fused with RRF.

        Args:
            query: Natural language query.
            top_k: Max results to return (0 = use config default).
            collection: Filter by collection.
            doc_id: Optional filter by document ID.
            session_id: Optional session scope.

        Returns:
            List of SearchResult ordered by fused relevance score.
        """
        k = top_k or self.settings.rag_top_k
        coll = collection or self.settings.rag_collection

        # Generate query embedding
        query_embedding = await self._get_query_embedding(query)

        # Run retrieval methods sequentially (AsyncSession does not support
        # concurrent operations on the same connection).

        # 1. Vector search (if embedding available)
        if query_embedding:
            vector_results = await self._vector_search(query_embedding, k * 2, coll, doc_id)
        else:
            vector_results = []

        # 2. Full-text search
        fts_results = await self._fts_search(query, k * 2, coll, doc_id)

        # 3. Graph-based search
        graph_results = await self._graph_search(query, k, coll)

        # Log retrieval stats
        logger.info(
            "hybrid_retrieval",
            query=query[:80],
            vector=len(vector_results),
            fts=len(fts_results),
            graph=len(graph_results),
        )

        # Fuse results with RRF
        fused = self._reciprocal_rank_fusion(
            vector_results=vector_results,
            fts_results=fts_results,
            graph_results=graph_results,
        )

        # Cross-encoder reranking: re-score the top fused candidates jointly
        # against the query for much higher precision. Honest no-op (keeps fused
        # order) when the reranker model is unavailable.
        if self.settings.rag_rerank_enabled and len(fused) > 1:
            try:
                from app.rag.reranker import rerank
                candidates = fused[: self.settings.rag_rerank_candidates]
                reranked = await rerank(query, candidates, top_k=k)
                if reranked:
                    return reranked
            except Exception as e:  # noqa: BLE001 — never fail retrieval on rerank
                logger.warning("rerank_skipped", error=str(e)[:200])

        return fused[:k]

    async def vector_only(
        self,
        query: str,
        top_k: int = 5,
        collection: str = "",
        doc_id: Optional[str] = None,
    ) -> list[SearchResult]:
        """Vector-only search (skip FTS and graph)."""
        coll = collection or self.settings.rag_collection
        embedding = await self._get_query_embedding(query)
        if not embedding:
            return []
        return await self._vector_search(embedding, top_k, coll, doc_id)

    def _reciprocal_rank_fusion(
        self,
        vector_results: list[SearchResult],
        fts_results: list[SearchResult],
        graph_results: list[SearchResult],
    ) -> list[SearchResult]:
        """Fuse results from multiple retrievers using RRF.

        score(doc) = Σ weight_i / (RRF_K + rank_i) for each retriever i
        """
        w_vec = self.settings.rag_vector_weight
        w_fts = self.settings.rag_fts_weight
        w_graph = self.settings.rag_graph_weight

        # Build content → (best_result, rrf_score) map
        scores: dict[str, tuple[SearchResult, float]] = {}

        def _add_results(results: list[SearchResult], weight: float) -> None:
            for rank, result in enumerate(results):
                key = result.content[:200]  # Dedup key
                rrf_score = weight / (RRF_K + rank + 1)
                if key in scores:
                    existing_result, existing_score = scores[key]
                    scores[key] = (existing_result, existing_score + rrf_score)
                else:
                    scores[key] = (result, rrf_score)

        _add_results(vector_results, w_vec)
        _add_results(fts_results, w_fts)
        _add_results(graph_results, w_graph)

        # Sort by fused score descending
        fused = sorted(scores.values(), key=lambda x: x[1], reverse=True)

        return [
            SearchResult(
                content=result.content,
                score=score,
                source=result.source,
                doc_id=result.doc_id,
                chunk_index=result.chunk_index,
                section_title=result.section_title,
                metadata=result.metadata,
                retrieval_method=f"hybrid(v={len(vector_results)},f={len(fts_results)},g={len(graph_results)})",
            )
            for result, score in fused
        ]

    async def _vector_search(
        self,
        query_embedding: list[float],
        top_k: int,
        collection: str,
        doc_id: Optional[str],
    ) -> list[SearchResult]:
        """Vector similarity search."""
        try:
            return await self.store.search(
                query_embedding=query_embedding,
                top_k=top_k,
                collection=collection,
                doc_id=doc_id,
            )
        except Exception as e:
            logger.warning("vector_search_error", error=str(e))
            return []

    async def _fts_search(
        self,
        query: str,
        top_k: int,
        collection: str,
        doc_id: Optional[str],
    ) -> list[SearchResult]:
        """Full-text search via PostgreSQL tsvector."""
        try:
            return await self.store.fts_search(
                query=query,
                top_k=top_k,
                collection=collection,
                doc_id=doc_id,
            )
        except Exception as e:
            logger.warning("fts_search_error", error=str(e))
            return []

    async def _graph_search(
        self,
        query: str,
        top_k: int,
        collection: str,
    ) -> list[SearchResult]:
        """Knowledge graph-based retrieval."""
        try:
            from app.rag.graph_rag import GraphRAGRetriever
            graph_retriever = GraphRAGRetriever(self.db)
            graph_chunks = await graph_retriever.retrieve(query, top_k=top_k)
            return [
                SearchResult(
                    content=gc.content,
                    score=gc.score,
                    source=gc.entity,
                    doc_id="",
                    chunk_index=0,
                    section_title=f"{gc.entity} → {gc.relation} → {gc.related_entity}",
                    metadata=gc.metadata,
                    retrieval_method="graph",
                )
                for gc in graph_chunks
            ]
        except Exception as e:
            logger.debug("graph_search_unavailable", error=str(e))
            return []

    async def _get_query_embedding(self, query: str) -> Optional[list[float]]:
        """Generate embedding for query text."""
        try:
            from app.xskill.embedding import generate_embedding
            return await generate_embedding(query[:8000])
        except Exception as e:
            logger.warning("query_embedding_failed", error=str(e))
            return None


def format_retrieval_context(results: list[SearchResult], max_chars: int = 8000) -> str:
    """Format search results into a context string for LLM injection.

    Args:
        results: List of SearchResult from retriever.
        max_chars: Max total characters in context.

    Returns:
        Formatted context string with source citations.
    """
    if not results:
        return ""

    parts: list[str] = []
    total = 0

    for i, r in enumerate(results, 1):
        source_info = f"[Source: {r.source}" if r.source else "[Source: unknown"
        if r.section_title:
            source_info += f" > {r.section_title}"
        source_info += f" | score: {r.score:.3f}]"

        chunk_text = f"--- Chunk {i} {source_info} ---\n{r.content}"

        if total + len(chunk_text) > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                chunk_text = chunk_text[:remaining] + "\n... [truncated]"
                parts.append(chunk_text)
            break

        parts.append(chunk_text)
        total += len(chunk_text)

    return "\n\n".join(parts)
