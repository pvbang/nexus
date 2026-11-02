"""
Nexus - Document Index Pipeline.

Orchestrates: chunk → embed → store in PostgreSQL pgvector.
Auto-indexes uploaded documents that exceed the threshold.

Usage::

    from app.rag.indexer import DocumentIndexer
    indexer = DocumentIndexer(db_session)
    result = await indexer.index_document(markdown_text, "report.pdf", session_id="abc")
"""

from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from typing import Any, Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.background_metrics import get_background_metrics
from app.rag.chunker import SmartChunker
from app.rag.models import Chunk, IndexResult, IndexedDocument
from app.rag.vector_store import PgVectorStore

logger = structlog.get_logger(__name__)


class DocumentIndexer:
    """Index documents into PostgreSQL pgvector for RAG retrieval."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings()
        self.chunker = SmartChunker(
            chunk_size=self.settings.rag_chunk_size,
            chunk_overlap=self.settings.rag_chunk_overlap,
        )
        self.store = PgVectorStore(db)

    async def index_document(
        self,
        content: str,
        filename: str,
        session_id: str = "",
        original_path: str = "",
        file_size: int = 0,
        mime_type: str = "",
        collection: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> IndexResult:
        """Index a document: chunk → embed → store.

        Args:
            content: Full document text (markdown).
            filename: Source filename.
            session_id: Chat session ID for scoping.
            original_path: Original file path on disk.
            file_size: File size in bytes.
            mime_type: MIME type.
            collection: Vector store collection name.
            metadata: Extra metadata.

        Returns:
            IndexResult with status and chunk count.
        """
        start = time.monotonic()
        doc_id = uuid.uuid4().hex[:16]
        coll = collection or self.settings.rag_collection

        if not content or not content.strip():
            return IndexResult(
                doc_id=doc_id, filename=filename,
                status="skipped", error="Empty content",
            )

        try:
            # 1. Chunk the document
            chunks = self.chunker.chunk_document(
                text=content,
                source=filename,
                doc_id=doc_id,
                metadata=metadata,
            )

            if not chunks:
                return IndexResult(
                    doc_id=doc_id, filename=filename,
                    status="skipped", error="No chunks produced",
                )

            # 1b. Contextual Retrieval (adaptive): for large docs, prepend an
            # LLM-generated situating context to each chunk so search can find
            # it even when the query omits surrounding context. Degrades
            # honestly (chunks unchanged) if the LLM is unavailable.
            from app.rag.contextual import contextualize_chunks, should_contextualize

            if should_contextualize(content):
                chunks = await contextualize_chunks(chunks, full_document=content)

            # 2. Generate embeddings for each chunk
            chunks = await self._embed_chunks(chunks)

            # 3. Store chunks in pgvector
            await self.store.add_chunks(chunks, collection=coll)

            # 4. Record indexed document metadata
            await self._save_indexed_doc(
                doc_id=doc_id,
                session_id=session_id,
                filename=filename,
                original_path=original_path,
                file_size=file_size,
                mime_type=mime_type,
                chunk_count=len(chunks),
                total_chars=len(content),
                collection=coll,
                metadata=metadata,
            )

            elapsed = (time.monotonic() - start) * 1000
            logger.info(
                "document_indexed",
                doc_id=doc_id,
                filename=filename,
                chunks=len(chunks),
                chars=len(content),
                elapsed_ms=round(elapsed),
            )

            # Fire-and-forget: generate ontology entities for the knowledge graph
            if len(content) >= 500:
                asyncio.ensure_future(
                    _generate_ontology_for_graph(content, filename, doc_id)
                )

            return IndexResult(
                doc_id=doc_id,
                filename=filename,
                chunk_count=len(chunks),
                total_chars=len(content),
                status="indexed",
                duration_ms=elapsed,
            )

        except Exception as e:
            logger.error("document_index_failed", filename=filename, error=str(e))
            return IndexResult(
                doc_id=doc_id, filename=filename,
                status="failed", error=str(e),
                duration_ms=(time.monotonic() - start) * 1000,
            )

    async def delete_document(self, doc_id: str, collection: str = "") -> bool:
        """Delete all chunks and metadata for a document."""
        coll = collection or self.settings.rag_collection
        deleted = await self.store.delete_by_doc_id(doc_id, collection=coll)

        # Update indexed_documents status
        await self.db.execute(
            text("""
                UPDATE indexed_documents SET status = 'deleted'
                WHERE doc_id = :doc_id
            """),
            {"doc_id": doc_id},
        )
        await self.db.commit()
        return deleted > 0

    async def list_indexed(self, session_id: str = "") -> list[dict[str, Any]]:
        """List all indexed documents, optionally filtered by session."""
        query = "SELECT * FROM indexed_documents WHERE status = 'indexed'"
        params: dict[str, Any] = {}
        if session_id:
            query += " AND session_id = :session_id"
            params["session_id"] = session_id
        query += " ORDER BY created_at DESC"

        result = await self.db.execute(text(query), params)
        return [dict(row._mapping) for row in result.fetchall()]

    async def _embed_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Generate embeddings for chunks using the embedding service."""
        try:
            from app.xskill.embedding import generate_embedding

            for chunk in chunks:
                # Truncate to avoid token limits
                embed_text = chunk.content[:8000]
                embedding = await generate_embedding(embed_text)
                if embedding:
                    chunk.embedding = embedding

            embedded = sum(1 for c in chunks if c.embedding is not None)
            logger.info("chunks_embedded", total=len(chunks), embedded=embedded)

        except Exception as e:
            logger.warning("embedding_failed", error=str(e))
            # Chunks stored without embeddings — FTS still works

        return chunks

    async def _save_indexed_doc(
        self,
        doc_id: str,
        session_id: str,
        filename: str,
        original_path: str,
        file_size: int,
        mime_type: str,
        chunk_count: int,
        total_chars: int,
        collection: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Save document metadata to indexed_documents table."""
        import json

        await self.db.execute(
            text("""
                INSERT INTO indexed_documents
                    (id, doc_id, session_id, filename, original_path,
                     file_size, mime_type, chunk_count, total_chars,
                     collection, status, metadata)
                VALUES
                    (gen_random_uuid(), :doc_id, :session_id, :filename, :original_path,
                     :file_size, :mime_type, :chunk_count, :total_chars,
                     :collection, 'indexed', :metadata::jsonb)
            """),
            {
                "doc_id": doc_id,
                "session_id": session_id,
                "filename": filename,
                "original_path": original_path,
                "file_size": file_size,
                "mime_type": mime_type,
                "chunk_count": chunk_count,
                "total_chars": total_chars,
                "collection": collection,
                "metadata": json.dumps(metadata or {}, ensure_ascii=False),
            },
        )
        await self.db.commit()


# ── Convenience: should this document be auto-indexed? ─────────

def should_auto_index(content: str) -> bool:
    """Check if content is long enough to benefit from RAG indexing."""
    settings = get_settings()
    return len(content) > settings.rag_auto_index_threshold


async def _generate_ontology_for_graph(
    content: str, filename: str, doc_id: str
) -> None:
    """
    Fire-and-forget: extract ontology from document and store entities in KG.

    Never raises — all errors are caught, logged, and tracked via background_metrics.
    """
    metrics = get_background_metrics()
    task_name = "ontology_generation"

    try:
        from app.memory.ontology_generator import get_ontology_generator
        from app.memory.graph_updater import get_graph_updater

        generator = get_ontology_generator()
        ontology = await generator.generate_ontology(text=content, domain_hint="")

        # Store ontology entity types and relation types as knowledge graph nodes
        graph_updater = get_graph_updater()
        async with graph_updater._db_scope() as mm:
            if not mm:
                logger.warning(
                    "ontology_kg_skipped_no_session",
                    doc_id=doc_id,
                    filename=filename,
                )
                metrics.record_failure(task_name, "No database session available")
                return

            # Store each entity type as a knowledge node
            entity_nodes = {}
            for et in ontology.entity_types:
                node = await mm.add_knowledge_node(
                    name=et.name,
                    entity_type="ontology_entity_type",
                    description=et.description,
                    properties={
                        "attributes": et.attributes,
                        "domain": ontology.domain,
                        "source_doc_id": doc_id,
                        "source_filename": filename,
                    },
                )
                entity_nodes[et.name.lower()] = node

            # Store each relation type as an edge between entity type nodes
            for rt in ontology.relation_types:
                source_node = entity_nodes.get(rt.source_type.lower())
                target_node = entity_nodes.get(rt.target_type.lower())
                if source_node and target_node:
                    await mm.add_knowledge_edge(
                        source_id=source_node.id,
                        target_id=target_node.id,
                        relation_type=rt.name,
                        weight=1.0,
                        properties={
                            "description": rt.description,
                            "domain": ontology.domain,
                            "source_doc_id": doc_id,
                        },
                    )

        logger.info(
            "ontology_kg_stored",
            doc_id=doc_id,
            filename=filename,
            entity_types=len(ontology.entity_types),
            relation_types=len(ontology.relation_types),
            domain=ontology.domain,
        )
        metrics.record_success(task_name)

    except Exception as exc:
        logger.error(
            "ontology_kg_failed",
            doc_id=doc_id,
            filename=filename,
            error=str(exc),
            traceback=traceback.format_exc(),
        )
        metrics.record_failure(task_name, str(exc))
