"""
MekongAI Nexus - Contextual Retrieval (Anthropic technique).

Prepends a short, LLM-generated situating context to each chunk BEFORE it is
embedded and full-text indexed. A chunk like "Revenue grew 3%" becomes
"[This chunk is from ACME's Q2 2026 report, discussing the cloud division.]
Revenue grew 3%", so both vector and keyword search can locate it from a
query that never mentions the surrounding context.

Reference: Anthropic, "Introducing Contextual Retrieval" (2024) — combining
contextual embeddings + contextual BM25 cut retrieval failures by ~49%, and
~67% when stacked with reranking.

Design choices for this codebase:
- Adaptive: only runs for documents past a size threshold (small docs don't
  benefit enough to justify the LLM calls).
- Bounded: caps the number of per-document LLM calls (rag_contextual_max_chunks).
- Honest fallback: if the LLM is unavailable or a call fails, the chunk is left
  unchanged rather than fabricating context. Indexing always proceeds.
- The situating context is stored separately in chunk.metadata["context"] so the
  original chunk text remains recoverable.
"""

from __future__ import annotations

import asyncio

import structlog

from app.config import get_settings
from app.rag.models import Chunk

logger = structlog.get_logger(__name__)

# Kept short and strict — we want 1-2 sentences, not a summary of the chunk.
_CONTEXT_PROMPT = (
    "<document>\n{document}\n</document>\n\n"
    "Here is a chunk we want to situate within the whole document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Give a short, succinct context (1-2 sentences, max ~80 tokens) to situate "
    "this chunk within the overall document, so it can be found by search. "
    "Answer ONLY with the context, no preamble, no quotes."
)

# Cap the document text we feed per call so a huge file doesn't blow the prompt.
_MAX_DOC_CHARS_FOR_CONTEXT = 100_000


def should_contextualize(content: str) -> bool:
    """Whether a document is large enough to benefit from contextualization."""
    settings = get_settings()
    if not settings.rag_contextual_enabled:
        return False
    return len(content) >= settings.rag_contextual_threshold


async def _generate_context(llm, document: str, chunk_text: str) -> str:
    """Ask the LLM for a one-line situating context. Returns '' on any failure."""
    prompt = _CONTEXT_PROMPT.format(
        document=document[:_MAX_DOC_CHARS_FOR_CONTEXT],
        chunk=chunk_text[:4000],
    )
    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=128,
        )
        return (result.get("content", "") or "").strip()
    except Exception as e:  # noqa: BLE001 — never let one chunk break indexing
        logger.debug("contextual_chunk_failed", error=str(e)[:200])
        return ""


async def contextualize_chunks(
    chunks: list[Chunk],
    full_document: str,
) -> list[Chunk]:
    """Prepend situating context to each chunk in place; returns the same list.

    Mutates chunk.content (prepended context) and records the raw context in
    chunk.metadata["context"]. If the LLM is unavailable, chunks are returned
    unchanged — indexing must still succeed (honest degradation).
    """
    settings = get_settings()
    if not chunks:
        return chunks

    try:
        from app.services.llm_client import get_core_llm
        llm = get_core_llm()
    except Exception as e:  # noqa: BLE001
        logger.warning("contextual_llm_unavailable", error=str(e)[:200])
        return chunks

    # Bound the number of LLM calls for very large documents.
    cap = settings.rag_contextual_max_chunks
    target = chunks[:cap] if len(chunks) > cap else chunks
    if len(chunks) > cap:
        logger.info(
            "contextual_chunk_cap_applied",
            total=len(chunks), contextualized=cap,
        )

    # Run with bounded concurrency so we don't hammer the provider.
    sem = asyncio.Semaphore(5)

    async def _one(chunk: Chunk) -> None:
        async with sem:
            ctx = await _generate_context(llm, full_document, chunk.content)
        if ctx:
            chunk.metadata["context"] = ctx
            # Prepend context; keep original text intact and recoverable.
            chunk.content = f"{ctx}\n\n{chunk.content}"

    await asyncio.gather(*(_one(c) for c in target), return_exceptions=True)

    contextualized = sum(1 for c in target if c.metadata.get("context"))
    logger.info(
        "chunks_contextualized",
        total=len(chunks), contextualized=contextualized,
    )
    return chunks
