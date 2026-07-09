"""
MekongAI Nexus - Cross-Encoder Reranker.

After hybrid fusion (vector + FTS + graph → RRF) produces a candidate set,
a cross-encoder re-scores each (query, chunk) PAIR jointly. Unlike the
bi-encoder used for embeddings — which encodes query and chunk separately —
a cross-encoder reads them together, so it judges relevance far more
precisely. This is the single highest-leverage precision win in modern RAG
and is what pushes Anthropic's Contextual Retrieval from ~49% to ~67% fewer
retrieval failures.

Honest degradation: if sentence-transformers / the model is unavailable, the
candidates are returned in their original fused order (no reordering, no
fabricated scores). Retrieval still works; it just isn't reranked.

The model loads lazily and is cached process-wide (first call pays the cost).
"""

from __future__ import annotations

import asyncio

import structlog

from app.config import get_settings
from app.rag.models import SearchResult

logger = structlog.get_logger(__name__)

# Process-wide singleton: (model_name) -> CrossEncoder | None (None = unavailable)
_MODEL_CACHE: dict[str, object] = {}
_LOAD_LOCK = asyncio.Lock()


async def _get_model(model_name: str):
    """Lazily load and cache the cross-encoder. Returns None if unavailable."""
    if model_name in _MODEL_CACHE:
        return _MODEL_CACHE[model_name]

    async with _LOAD_LOCK:
        if model_name in _MODEL_CACHE:  # double-checked after acquiring lock
            return _MODEL_CACHE[model_name]

        def _load():
            from sentence_transformers import CrossEncoder
            return CrossEncoder(model_name)

        try:
            # Loading touches disk/network and is CPU-bound → off the event loop.
            model = await asyncio.to_thread(_load)
            _MODEL_CACHE[model_name] = model
            logger.info("reranker_model_loaded", model=model_name)
            return model
        except Exception as e:  # noqa: BLE001 — missing dep / model is non-fatal
            logger.warning("reranker_unavailable", model=model_name, error=str(e)[:200])
            _MODEL_CACHE[model_name] = None  # cache the failure; don't retry every call
            return None


def is_available(model_name: str = "") -> bool:
    """Best-effort check whether sentence-transformers is importable."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except Exception:
        return False


async def rerank(
    query: str,
    results: list[SearchResult],
    top_k: int,
) -> list[SearchResult]:
    """Re-score and reorder results by cross-encoder relevance to the query.

    Returns the top_k results. If the model is unavailable, returns the first
    top_k of the input order unchanged (honest no-op).
    """
    settings = get_settings()
    if not settings.rag_rerank_enabled or not results:
        return results[:top_k] if top_k else results

    model = await _get_model(settings.rag_rerank_model)
    if model is None:
        # Honest degradation: keep fused order, just trim.
        return results[:top_k] if top_k else results

    pairs = [[query, r.content] for r in results]

    def _score():
        # CrossEncoder.predict returns a numpy array of relevance scores.
        return model.predict(pairs)

    try:
        scores = await asyncio.to_thread(_score)
    except Exception as e:  # noqa: BLE001
        logger.warning("rerank_predict_failed", error=str(e)[:200])
        return results[:top_k] if top_k else results

    ranked = sorted(
        zip(results, scores),
        key=lambda rs: float(rs[1]),
        reverse=True,
    )

    out: list[SearchResult] = []
    for r, s in ranked:
        r.score = float(s)
        r.retrieval_method = f"{r.retrieval_method}+rerank"
        out.append(r)

    logger.info("reranked", candidates=len(results), returned=min(top_k or len(out), len(out)))
    return out[:top_k] if top_k else out
