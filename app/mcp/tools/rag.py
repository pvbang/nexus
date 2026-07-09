"""
MekongAI Nexus - RAG Search & Index Tools (Rewritten).

Uses PostgreSQL pgvector via the new app.rag pipeline.
Replaces the old get_vector_store() approach with proper DB session.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


def _get_db_session():
    """Get an async DB session context manager for RAG operations."""
    from app.core.dependencies import get_async_session
    return get_async_session()


async def _handle_rag_search(args: dict[str, Any], **kwargs: Any) -> str:
    """Search indexed documents using hybrid retrieval (vector + FTS + graph)."""
    query = args.get("query", "").strip()
    limit = args.get("limit", 5)
    collection = args.get("collection", "")
    doc_id = args.get("doc_id", "")

    if not query:
        return tool_error("Search query is required")

    try:
        from app.rag.retriever import HybridRetriever, format_retrieval_context

        async with await _get_db_session() as db:
            retriever = HybridRetriever(db)
            results = await retriever.retrieve(
                query=query,
                top_k=limit,
                collection=collection or "",
                doc_id=doc_id or None,
            )

            if not results:
                return tool_result(
                    success=True,
                    output=f"No documents found matching: '{query}'",
                    data={"results": [], "count": 0},
                )

            # Format for display
            context = format_retrieval_context(results, max_chars=6000)

            formatted_results = [
                {
                    "content": r.content[:500],
                    "score": r.score,
                    "source": r.source,
                    "section_title": r.section_title,
                    "retrieval_method": r.retrieval_method,
                }
                for r in results
            ]

            return tool_result(
                success=True,
                output=f"📚 **Found {len(results)} relevant chunks for '{query}':**\n\n{context}",
                data={"results": formatted_results, "count": len(results), "query": query},
            )

    except Exception as e:
        logger.error("rag_search_error", error=str(e), query=query[:100])
        return tool_error(f"RAG search failed: {e}")


async def _handle_rag_index(args: dict[str, Any], **kwargs: Any) -> str:
    """Index a document into the vector database for later retrieval."""
    content = args.get("content", "").strip()
    source = args.get("source", "manual")
    collection = args.get("collection", "")
    metadata = args.get("metadata", {})

    if not content:
        return tool_error("Document content is required")

    if len(content) > 200_000:
        return tool_error("Document too large (max 200K chars)")

    try:
        from app.rag.indexer import DocumentIndexer

        async with await _get_db_session() as db:
            indexer = DocumentIndexer(db)
            result = await indexer.index_document(
                content=content,
                filename=source,
                collection=collection or "",
                metadata=metadata if isinstance(metadata, dict) else {},
            )

            if result.status == "indexed":
                return tool_result(
                    success=True,
                    output=(
                        f"📥 Document indexed: {result.filename}\n"
                        f"   Chunks: {result.chunk_count} | Chars: {result.total_chars:,}\n"
                        f"   Doc ID: {result.doc_id} | Time: {result.duration_ms:.0f}ms"
                    ),
                    data={
                        "doc_id": result.doc_id,
                        "chunks": result.chunk_count,
                        "chars": result.total_chars,
                        "collection": collection or "documents",
                    },
                )
            else:
                return tool_error(f"Indexing failed: {result.error}")

    except Exception as e:
        logger.error("rag_index_error", error=str(e))
        return tool_error(f"Indexing failed: {e}")


async def _handle_document_search(args: dict[str, Any], **kwargs: Any) -> str:
    """Search across all uploaded and indexed documents."""
    query = args.get("query", "").strip()
    limit = args.get("limit", 5)

    if not query:
        return tool_error("Search query is required")

    try:
        from app.rag.retriever import HybridRetriever, format_retrieval_context

        async with await _get_db_session() as db:
            retriever = HybridRetriever(db)
            results = await retriever.retrieve(query=query, top_k=limit)

            if not results:
                return tool_result(
                    success=True,
                    output=f"No documents found matching: '{query}'",
                    data={"results": [], "count": 0},
                )

            context = format_retrieval_context(results, max_chars=8000)
            return tool_result(
                success=True,
                output=f"📚 Found {len(results)} relevant document chunks:\n\n{context}",
                data={"count": len(results)},
            )

    except Exception as e:
        logger.error("document_search_error", error=str(e))
        return tool_error(f"Document search failed: {e}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="rag_search",
    toolset="memory",
    schema={
        "description": (
            "Search through indexed documents using hybrid retrieval "
            "(vector similarity + full-text search + knowledge graph). "
            "Use for finding relevant information from uploaded documents, "
            "knowledge bases, or previously indexed content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (natural language)"},
                "limit": {"type": "integer", "description": "Max results (default: 5)"},
                "collection": {"type": "string", "description": "Collection name (default: 'documents')"},
                "doc_id": {"type": "string", "description": "Optional: search within a specific document"},
            },
            "required": ["query"],
        },
    },
    handler=_handle_rag_search,
    is_async=True,
    emoji="📚",
)

registry.register(
    name="rag_index",
    toolset="memory",
    schema={
        "description": (
            "Index a document into the vector database for later retrieval via rag_search. "
            "Automatically chunks the document, generates embeddings, and stores in PostgreSQL pgvector."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Document text content to index"},
                "source": {"type": "string", "description": "Source identifier (e.g., filename, URL)"},
                "collection": {"type": "string", "description": "Collection name (default: 'documents')"},
                "metadata": {"type": "object", "description": "Additional metadata key-value pairs"},
            },
            "required": ["content"],
        },
    },
    handler=_handle_rag_index,
    is_async=True,
    emoji="📥",
)

registry.register(
    name="document_search",
    toolset="memory",
    schema={
        "description": (
            "Search across ALL uploaded and indexed documents using hybrid retrieval. "
            "Finds relevant content from PDFs, DOCX, code files, and other documents "
            "that were uploaded in any session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for (natural language)"},
                "limit": {"type": "integer", "description": "Max results (default: 5)"},
            },
            "required": ["query"],
        },
    },
    handler=_handle_document_search,
    is_async=True,
    emoji="🔍",
)
