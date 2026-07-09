"""Smart Context Aggregation — parallel fetch, relevance scoring, dedup."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class ContextPiece:
    """A single piece of context from any source."""

    content: str
    source: str  # "user_memory", "active_recall", "rag", "knowledge_graph", "history", "xskill"
    score: float = 0.0
    timestamp: float = 0.0  # when the underlying data was created/updated
    metadata: dict = field(default_factory=dict)


@dataclass
class ContextSource:
    """Configuration for a context source."""

    name: str
    fetcher: Callable[..., Awaitable[Optional[str]]]  # async function that returns context string
    priority: float  # 0.0 to 1.0, higher = more important
    timeout: float = 30.0  # seconds


class SmartContextAggregator:
    """
    Aggregates context from multiple sources with parallel fetch,
    relevance scoring, deduplication, and budget enforcement.

    Usage:
        aggregator = SmartContextAggregator(budget_chars=8000)
        aggregator.add_source("user_memory", fetch_user_memory, priority=1.0)
        aggregator.add_source("active_recall", fetch_recall, priority=0.9)
        aggregator.add_source("rag", fetch_rag, priority=0.7)
        ...

        result = await aggregator.aggregate(user_query="build me a REST API")
        # result is a formatted string ready to inject into prompt
    """

    # Source priority map (default)
    DEFAULT_PRIORITIES = {
        "user_memory": 1.0,
        "active_recall": 0.9,
        "rag": 0.7,
        "knowledge_graph": 0.6,
        "history": 0.5,
        "xskill": 0.4,
        "memory_layers": 0.55,
    }

    def __init__(self, budget_chars: int = 8000, dedup_threshold: float = 0.6):
        self._sources: List[ContextSource] = []
        self._budget_chars = budget_chars
        self._dedup_threshold = dedup_threshold

    def add_source(
        self,
        name: str,
        fetcher: Callable[..., Awaitable[Optional[str]]],
        priority: Optional[float] = None,
        timeout: float = 30.0,
    ) -> None:
        """Register a context source."""
        if priority is None:
            priority = self.DEFAULT_PRIORITIES.get(name, 0.3)
        self._sources.append(
            ContextSource(name=name, fetcher=fetcher, priority=priority, timeout=timeout)
        )

    async def aggregate(self, user_query: str, **kwargs) -> str:
        """
        Fetch all sources in parallel, score, dedup, and return budget-trimmed context.

        Returns formatted string ready for prompt injection.
        """
        # 1. Parallel fetch with timeout
        pieces = await self._fetch_all(user_query, **kwargs)

        if not pieces:
            return ""

        # 2. Score each piece
        self._score_pieces(pieces, user_query)

        # 3. Sort by score descending
        pieces.sort(key=lambda p: p.score, reverse=True)

        # 4. Dedup
        pieces = self._deduplicate(pieces)

        # 5. Budget enforcement — greedy top-K
        selected = self._apply_budget(pieces)

        # 6. Log performance metrics
        total_score = sum(p.score for p in selected)
        budget_used = sum(len(p.content) for p in selected)
        logger.info(
            "smart_context_aggregated",
            extra={
                "sources_registered": len(self._sources),
                "sources_returned": len(pieces),
                "pieces_selected": len(selected),
                "total_score": round(total_score, 3),
                "budget_used": budget_used,
                "budget_limit": self._budget_chars,
                "budget_pct": round(budget_used / self._budget_chars * 100, 1) if self._budget_chars else 0,
            },
        )

        # 7. Format output
        return self._format_output(selected)

    async def _fetch_all(self, user_query: str, **kwargs) -> List[ContextPiece]:
        """Fetch all sources in parallel with per-source timeout."""

        async def _safe_fetch(source: ContextSource) -> Optional[ContextPiece]:
            try:
                result = await asyncio.wait_for(
                    source.fetcher(user_query, **kwargs),
                    timeout=source.timeout,
                )
                if result and result.strip():
                    return ContextPiece(
                        content=result.strip(),
                        source=source.name,
                        timestamp=time.time(),
                        metadata={"priority": source.priority},
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "context_source_timeout",
                    extra={"source": source.name, "timeout": source.timeout},
                )
            except Exception as e:
                logger.debug(
                    "context_source_failed",
                    extra={"source": source.name, "error": str(e)},
                )
            return None

        results = await asyncio.gather(
            *[_safe_fetch(s) for s in self._sources],
            return_exceptions=False,
        )
        return [r for r in results if r is not None]

    def _score_pieces(self, pieces: List[ContextPiece], query: str) -> None:
        """Score each piece based on keyword overlap, source priority, and length factor."""
        query_tokens = set(query.lower().split())

        for piece in pieces:
            # Keyword overlap (weight 0.4)
            content_tokens = set(piece.content.lower().split()[:200])  # cap for perf
            overlap = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
            keyword_score = min(overlap * 2, 1.0)  # normalize, cap at 1.0

            # Source priority (weight 0.4)
            priority_score = piece.metadata.get("priority", 0.3)

            # Length/conciseness factor (weight 0.2) — prefer concise but non-empty
            length = len(piece.content)
            if length < 50:
                length_score = 0.3
            elif length < 500:
                length_score = 1.0
            elif length < 2000:
                length_score = 0.8
            else:
                length_score = 0.5

            piece.score = (keyword_score * 0.4) + (priority_score * 0.4) + (length_score * 0.2)

    def _deduplicate(self, pieces: List[ContextPiece]) -> List[ContextPiece]:
        """Remove pieces with >threshold token overlap with higher-scored pieces."""
        if len(pieces) <= 1:
            return pieces

        kept: List[ContextPiece] = []
        for piece in pieces:
            piece_tokens = set(piece.content.lower().split()[:300])
            is_dup = False
            for existing in kept:
                existing_tokens = set(existing.content.lower().split()[:300])
                if not piece_tokens or not existing_tokens:
                    continue
                intersection = len(piece_tokens & existing_tokens)
                denominator = min(len(piece_tokens), len(existing_tokens))
                if denominator > 0 and (intersection / denominator) > self._dedup_threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(piece)
        return kept

    def _apply_budget(self, pieces: List[ContextPiece]) -> List[ContextPiece]:
        """Greedy select top pieces until budget exhausted."""
        selected: List[ContextPiece] = []
        used_chars = 0
        for piece in pieces:
            piece_len = len(piece.content)
            if used_chars + piece_len <= self._budget_chars:
                selected.append(piece)
                used_chars += piece_len
            elif used_chars == 0:
                # At least include first piece even if over budget (truncated)
                piece.content = piece.content[: self._budget_chars]
                selected.append(piece)
                break
        return selected

    def _format_output(self, pieces: List[ContextPiece]) -> str:
        """Format selected pieces into prompt-ready string."""
        if not pieces:
            return ""

        parts = []
        for piece in pieces:
            parts.append(f"[{piece.source}] {piece.content}")

        return "\n\n".join(parts)
