"""
Nexus - Search Orchestrator.

Coordinates multi-engine search, deduplicates results, applies
freshness + credibility scoring, and returns unified sorted results.

Usage::

    orchestrator = SearchOrchestrator()
    response = await orchestrator.search("latest AI news", mode="news")
    for r in response.results:
        print(f"{r.title} — {r.url} (score: {r.raw_score})")

Modes:
    - general: Multi-engine web search (DDG + Brave + Tavily + Serper)
    - news: News-focused search (DDG News + Serper News + Brave)
    - academic: Academic papers (Semantic Scholar + arXiv)
    - code: Code/tech search (DDG + StackOverflow via Serper)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional
from urllib.parse import urlparse

import structlog

from app.mcp.tools.search_engines import (
    SearchEngine,
    SearchResponse,
    SearchResult,
    get_available_engines,
    get_engine,
)
from app.mcp.tools.search_scoring import ResultScorer

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Engine selection per mode
# ------------------------------------------------------------------

# Which engines to use for each search mode (in priority order).
# Free (no API key) engines are listed first for zero-config operation.
# Paid engines are appended as optional boosters when keys are present.
_MODE_ENGINES: dict[str, list[str]] = {
    "general": ["duckduckgo", "wikipedia", "brave", "tavily", "serper"],
    "news": ["duckduckgo_news", "duckduckgo", "serper", "brave"],
    "academic": ["semantic_scholar", "arxiv", "core", "google_scholar"],
    "code": ["github", "duckduckgo", "serper"],
    "knowledge": ["wikipedia", "wikidata", "duckduckgo"],
    "medical": ["pubmed", "semantic_scholar", "duckduckgo"],
    "security": ["nvd", "exploitdb", "github", "duckduckgo", "google_scholar"],
}

# Scorer weight presets per mode
_MODE_WEIGHTS: dict[str, dict[str, float]] = {
    "general": {"weight_freshness": 0.30, "weight_credibility": 0.35, "weight_relevance": 0.35},
    "news": {"weight_freshness": 0.55, "weight_credibility": 0.25, "weight_relevance": 0.20},
    "academic": {"weight_freshness": 0.15, "weight_credibility": 0.45, "weight_relevance": 0.40},
    "code": {"weight_freshness": 0.20, "weight_credibility": 0.30, "weight_relevance": 0.50},
    "knowledge": {"weight_freshness": 0.10, "weight_credibility": 0.50, "weight_relevance": 0.40},
    "medical": {"weight_freshness": 0.20, "weight_credibility": 0.50, "weight_relevance": 0.30},
    "security": {"weight_freshness": 0.35, "weight_credibility": 0.30, "weight_relevance": 0.35},
}

# Max engines to use in parallel (prevent excessive API calls)
_MAX_PARALLEL_ENGINES = 3


# ------------------------------------------------------------------
# Orchestrator
# ------------------------------------------------------------------


class SearchOrchestrator:
    """Orchestrate multi-engine search with dedup and scoring."""

    def __init__(self) -> None:
        self._scorer_cache: dict[str, ResultScorer] = {}

    def _get_scorer(self, mode: str) -> ResultScorer:
        """Get or create a scorer for the given mode."""
        if mode not in self._scorer_cache:
            weights = _MODE_WEIGHTS.get(mode, _MODE_WEIGHTS["general"])
            self._scorer_cache[mode] = ResultScorer(**weights)
        return self._scorer_cache[mode]

    async def search(
        self,
        query: str,
        *,
        mode: str = "general",
        max_results: int = 10,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        engines: Optional[list[str]] = None,
        sort_by: str = "relevance",  # "relevance" or "freshness"
    ) -> SearchResponse:
        """Execute multi-engine search and return scored, deduplicated results.

        Args:
            query: Search query string.
            mode: Search mode — "general", "news", "academic", "code".
            max_results: Maximum results to return after dedup.
            date_filter: Time filter — "day", "week", "month", "year".
            region: Region code — "vn", "us", etc.
            engines: Explicit engine names to use (overrides mode default).
            sort_by: Sort by "relevance" (combined score) or "freshness".

        Returns:
            SearchResponse with scored, deduplicated, sorted results.
        """
        start_time = time.monotonic()

        # Determine which engines to use
        available = get_available_engines()
        if engines:
            # User-specified engines
            engine_names = [e for e in engines if e in available]
        else:
            # Mode-based selection
            mode_engines = _MODE_ENGINES.get(mode, _MODE_ENGINES["general"])
            engine_names = [e for e in mode_engines if e in available]

        if not engine_names:
            # Ultimate fallback: try DuckDuckGo even if not in mode
            ddg = available.get("duckduckgo")
            if ddg:
                engine_names = ["duckduckgo"]
            else:
                return SearchResponse(
                    results=[],
                    engines_used=[],
                    engines_failed=[],
                    total_results=0,
                    query=query,
                    mode=mode,
                    search_time_ms=0,
                )

        # Limit parallel engines
        engine_names = engine_names[:_MAX_PARALLEL_ENGINES]

        # Per-engine result count: ask for more to account for dedup
        per_engine_count = max(max_results, 5)

        # Serper news mode flag
        extra_kwargs: dict[str, dict[str, Any]] = {}
        if mode == "news":
            extra_kwargs["serper"] = {"news_mode": True}

        # Execute searches in parallel
        engines_used: list[str] = []
        engines_failed: list[str] = []
        all_results: list[SearchResult] = []

        # Sentinel to distinguish "error" from "empty results"
        _SEARCH_ERROR = object()

        async def _run_engine(name: str) -> list[SearchResult] | object:
            engine = available[name]
            kwargs = extra_kwargs.get(name, {})
            try:
                results = await asyncio.wait_for(
                    engine.search(
                        query,
                        max_results=per_engine_count,
                        date_filter=date_filter,
                        region=region,
                        **kwargs,
                    ),
                    timeout=12.0,
                )
                return results
            except asyncio.TimeoutError:
                logger.warning("search_engine_timeout", engine=name, query=query[:80])
                return _SEARCH_ERROR  # type: ignore[return-value]
            except Exception as e:
                logger.warning("search_engine_error", engine=name, error=str(e))
                return _SEARCH_ERROR  # type: ignore[return-value]

        tasks = {name: asyncio.create_task(_run_engine(name)) for name in engine_names}

        for name, task in tasks.items():
            try:
                results = await task
                if results is _SEARCH_ERROR:
                    engines_failed.append(name)
                elif results:
                    all_results.extend(results)  # type: ignore[arg-type]
                    engines_used.append(name)
                else:
                    # Empty results is not a failure — engine worked but found nothing
                    engines_used.append(name)
            except Exception:
                engines_failed.append(name)

        # Deduplicate by URL
        deduped = self._deduplicate(all_results)

        # Score results
        scorer = self._get_scorer(mode)
        scored = scorer.score_and_sort(deduped)

        # Apply sort preference
        if sort_by == "freshness":
            from app.mcp.tools.search_scoring import FreshnessScorer
            freshness = FreshnessScorer()
            scored.sort(key=lambda x: freshness.score(x[0]), reverse=True)

        # Take top results and attach scores
        final_results: list[SearchResult] = []
        for result, score in scored[:max_results]:
            result.raw_score = score
            final_results.append(result)

        elapsed_ms = round((time.monotonic() - start_time) * 1000, 1)

        logger.info(
            "search_orchestrator_complete",
            query=query[:80],
            mode=mode,
            engines_used=engines_used,
            engines_failed=engines_failed,
            total_raw=len(all_results),
            deduped=len(deduped),
            returned=len(final_results),
            time_ms=elapsed_ms,
        )

        return SearchResponse(
            results=final_results,
            engines_used=engines_used,
            engines_failed=engines_failed,
            total_results=len(final_results),
            query=query,
            mode=mode,
            search_time_ms=elapsed_ms,
        )

    def _deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """Remove duplicate results by normalized URL.

        When duplicates are found, prefer the result with more metadata
        (published_date, longer snippet, etc.).
        Results with empty URLs are kept (not deduped) rather than silently dropped.
        """
        seen_urls: dict[str, SearchResult] = {}
        no_url_results: list[SearchResult] = []

        for r in results:
            norm_url = self._normalize_url(r.url)
            if not norm_url:
                no_url_results.append(r)
                continue

            existing = seen_urls.get(norm_url)
            if existing is None:
                seen_urls[norm_url] = r
            else:
                if self._result_richness(r) > self._result_richness(existing):
                    seen_urls[norm_url] = r

        return list(seen_urls.values()) + no_url_results

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalize URL for dedup comparison."""
        if not url:
            return ""
        try:
            parsed = urlparse(url)
            # Strip www., trailing slash, fragments, common tracking params
            host = parsed.netloc.lower()
            if host.startswith("www."):
                host = host[4:]
            path = parsed.path.rstrip("/")
            return f"{host}{path}"
        except Exception:
            return url.lower().strip("/")

    @staticmethod
    def _result_richness(r: SearchResult) -> int:
        """Score how "rich" a result is (more metadata = higher)."""
        score = 0
        if r.published_date:
            score += 3
        if len(r.snippet) > 100:
            score += 2
        if r.metadata:
            score += 1
        if r.raw_score > 0:
            score += 1
        return score


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_orchestrator: Optional[SearchOrchestrator] = None


def get_search_orchestrator() -> SearchOrchestrator:
    """Return the singleton SearchOrchestrator."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = SearchOrchestrator()
    return _orchestrator
