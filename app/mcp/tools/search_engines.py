"""
MekongAI Nexus - Multi-Engine Search Abstractions.

Provides abstract SearchEngine base class and concrete implementations
for multiple search providers. Each engine is optional — if API key is
not configured, the engine is simply skipped.

Engines:
    - DuckDuckGo (ddgs) — always available, no API key
    - Brave Search — requires BRAVE_API_KEY
    - Tavily AI Search — requires TAVILY_API_KEY
    - Serper (Google) — requires SERPER_API_KEY
    - Semantic Scholar — free, no key (academic)
    - arXiv — free, no key (academic)

All engines return standardized SearchResult objects.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs, unquote

import httpx
import structlog

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Data models
# ------------------------------------------------------------------


@dataclass
class SearchResult:
    """Standardized search result from any engine."""

    title: str
    url: str
    snippet: str
    source_engine: str
    published_date: Optional[str] = None  # ISO 8601 or human-readable
    domain: str = ""
    raw_score: float = 0.0  # Engine-specific relevance score
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.domain and self.url:
            try:
                self.domain = urlparse(self.url).netloc.lower()
            except Exception:
                self.domain = ""


@dataclass
class SearchResponse:
    """Aggregated response from search orchestrator."""

    results: list[SearchResult]
    engines_used: list[str]
    engines_failed: list[str]
    total_results: int
    query: str
    mode: str = "general"
    search_time_ms: float = 0.0


# ------------------------------------------------------------------
# Abstract base
# ------------------------------------------------------------------


class SearchEngine(ABC):
    """Abstract base for a search engine provider."""

    name: str = "base"

    @abstractmethod
    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Execute search and return standardized results."""

    def is_available(self) -> bool:
        """Check if this engine is configured and ready."""
        return True


# ------------------------------------------------------------------
# DuckDuckGo Engine (always available)
# ------------------------------------------------------------------


class DuckDuckGoEngine(SearchEngine):
    """DuckDuckGo search via ddgs library (no API key needed)."""

    name = "duckduckgo"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        try:
            from ddgs import DDGS

            timelimit = None
            if date_filter:
                timelimit_map = {"day": "d", "week": "w", "month": "m", "year": "y"}
                timelimit = timelimit_map.get(date_filter)

            ddg_region = region or "wt-wt"
            truncated_query = query[:500].strip()
            if not truncated_query:
                return []

            loop = asyncio.get_event_loop()
            raw_results = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: list(
                        DDGS(timeout=20).text(
                            truncated_query,
                            max_results=max_results,
                            timelimit=timelimit,
                            region=ddg_region,
                        )
                    ),
                ),
                timeout=25.0,
            )

            results: list[SearchResult] = []
            for r in raw_results:
                url = r.get("href", "")
                if not url:
                    continue
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=url,
                        snippet=r.get("body", ""),
                        source_engine=self.name,
                        published_date=None,
                    )
                )
            return results
        except ImportError:
            logger.warning("ddgs_not_installed")
            return await self._fallback_html_search(query, max_results)
        except Exception as e:
            logger.warning("ddgs_search_error", error=str(e), query=query[:100])
            return await self._fallback_html_search(query, max_results)

    async def _fallback_html_search(
        self, query: str, max_results: int
    ) -> list[SearchResult]:
        """Fallback: HTML scraping DuckDuckGo."""
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
                resp = await http.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    headers={"User-Agent": "MekongAI-Nexus/1.0"},
                )
                results: list[SearchResult] = []
                text = resp.text
                links = re.findall(
                    r'class="result__a" href="(.*?)".*?>(.*?)</a>',
                    text,
                    re.DOTALL,
                )
                snippets = re.findall(
                    r'class="result__snippet[^>]*>(.*?)</a>', text, re.DOTALL
                )

                for i, (raw_url, title) in enumerate(links[:max_results]):
                    snippet = snippets[i].strip() if i < len(snippets) else ""
                    snippet = re.sub(r"<.*?>", "", snippet)
                    title = re.sub(r"<.*?>", "", title)
                    url = self._extract_ddg_url(raw_url)
                    results.append(
                        SearchResult(
                            title=title.strip(),
                            url=url,
                            snippet=snippet,
                            source_engine=f"{self.name}_fallback",
                        )
                    )
                return results
        except Exception as e:
            logger.warning("ddg_html_fallback_failed", error=str(e))
            return []

    @staticmethod
    def _extract_ddg_url(raw_url: str) -> str:
        """Extract actual URL from DDG redirect."""
        if "uddg=" in raw_url:
            try:
                parsed = urlparse(
                    raw_url if raw_url.startswith("http") else f"https:{raw_url}"
                )
                params = parse_qs(parsed.query)
                uddg = params.get("uddg", [None])[0]
                if uddg:
                    return unquote(uddg)
            except Exception:
                pass
        if raw_url.startswith("//"):
            return f"https:{raw_url}"
        return raw_url


class DuckDuckGoNewsEngine(SearchEngine):
    """DuckDuckGo news search."""

    name = "duckduckgo_news"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        try:
            from ddgs import DDGS

            timelimit = None
            if date_filter:
                timelimit_map = {"day": "d", "week": "w", "month": "m"}
                timelimit = timelimit_map.get(date_filter, "w")

            loop = asyncio.get_event_loop()
            raw_results = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: list(
                        DDGS(timeout=20).news(
                            query,
                            max_results=max_results,
                            timelimit=timelimit,
                            region=region or "wt-wt",
                        )
                    ),
                ),
                timeout=25.0,
            )

            results: list[SearchResult] = []
            for r in raw_results:
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("body", ""),
                        source_engine=self.name,
                        published_date=r.get("date", None),
                        metadata={
                            "source": r.get("source", ""),
                            "image": r.get("image", ""),
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("ddg_news_error", error=str(e))
            return []


# ------------------------------------------------------------------
# Brave Search Engine
# ------------------------------------------------------------------


class BraveSearchEngine(SearchEngine):
    """Brave Search API — requires BRAVE_API_KEY."""

    name = "brave"
    _API_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self) -> None:
        self._api_key = os.environ.get("BRAVE_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        if not self._api_key:
            return []

        params: dict[str, Any] = {
            "q": query,
            "count": min(max_results, 20),
        }
        if date_filter:
            freshness_map = {
                "day": "pd",
                "week": "pw",
                "month": "pm",
                "year": "py",
            }
            freshness = freshness_map.get(date_filter)
            if freshness:
                params["freshness"] = freshness
        if region:
            params["country"] = region

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    self._API_URL,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": self._api_key,
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            web_results = data.get("web", {}).get("results", [])
            results: list[SearchResult] = []
            for r in web_results[:max_results]:
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("description", ""),
                        source_engine=self.name,
                        published_date=r.get("page_age", None),
                        metadata={
                            "age": r.get("age", ""),
                            "language": r.get("language", ""),
                            "family_friendly": r.get("family_friendly", True),
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("brave_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Tavily AI Search Engine
# ------------------------------------------------------------------


class TavilySearchEngine(SearchEngine):
    """Tavily AI Search — optimized for AI agents. Requires TAVILY_API_KEY."""

    name = "tavily"
    _API_URL = "https://api.tavily.com/search"

    def __init__(self) -> None:
        self._api_key = os.environ.get("TAVILY_API_KEY", "")

    def is_available(self) -> bool:
        return bool(self._api_key)

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        if not self._api_key:
            return []

        search_depth = kwargs.get("search_depth", "basic")
        include_answer = kwargs.get("include_answer", False)

        body: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": min(max_results, 10),
            "search_depth": search_depth,
            "include_answer": include_answer,
        }

        # Tavily supports days parameter for freshness
        if date_filter:
            days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
            days = days_map.get(date_filter)
            if days:
                body["days"] = days

        try:
            async with httpx.AsyncClient(timeout=20.0) as http:
                resp = await http.post(self._API_URL, json=body)
                resp.raise_for_status()
                data = resp.json()

            raw_results = data.get("results", [])
            results: list[SearchResult] = []
            for r in raw_results[:max_results]:
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("content", ""),
                        source_engine=self.name,
                        published_date=r.get("published_date", None),
                        raw_score=r.get("score", 0.0),
                        metadata={
                            "answer": data.get("answer", ""),
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("tavily_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Serper (Google Search) Engine
# ------------------------------------------------------------------


class SerperSearchEngine(SearchEngine):
    """Serper.dev — Google Search API wrapper. Requires SERPER_API_KEY."""

    name = "serper"
    _API_URL = "https://google.serper.dev/search"
    _NEWS_URL = "https://google.serper.dev/news"

    def __init__(self) -> None:
        self._api_key = os.environ.get("SERPER_API_KEY", "")
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0  # timestamp when circuit breaker resets

    def is_available(self) -> bool:
        if not self._api_key:
            return False
        # Circuit breaker: after 3 consecutive 4xx failures, disable for 5 minutes
        if self._consecutive_failures >= 3:
            import time as _time
            if _time.time() < self._circuit_open_until:
                return False
            # Reset after cooldown
            self._consecutive_failures = 0
        return True

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        if not self._api_key or not self.is_available():
            return []

        is_news = kwargs.get("news_mode", False)
        url = self._NEWS_URL if is_news else self._API_URL

        # Serper API rejects queries > 2048 chars with 400 Bad Request.
        # Truncate to 256 chars for reliability (long queries degrade results anyway).
        truncated_query = query[:256].strip()
        if not truncated_query:
            return []

        body: dict[str, Any] = {
            "q": truncated_query,
            "num": min(max_results, 20),
        }
        if date_filter:
            tbs_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
            tbs = tbs_map.get(date_filter)
            if tbs:
                body["tbs"] = tbs
        if region:
            body["gl"] = region

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.post(
                    url,
                    json=body,
                    headers={
                        "X-API-KEY": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            results: list[SearchResult] = []

            # Parse organic results
            organic = data.get("organic", [])
            for r in organic[:max_results]:
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("link", ""),
                        snippet=r.get("snippet", ""),
                        source_engine=self.name,
                        published_date=r.get("date", None),
                        metadata={
                            "position": r.get("position", 0),
                            "sitelinks": r.get("sitelinks", []),
                        },
                    )
                )

            # Parse news results
            news = data.get("news", [])
            for r in news[:max_results]:
                results.append(
                    SearchResult(
                        title=r.get("title", ""),
                        url=r.get("link", ""),
                        snippet=r.get("snippet", ""),
                        source_engine=f"{self.name}_news",
                        published_date=r.get("date", None),
                        metadata={
                            "source": r.get("source", ""),
                            "imageUrl": r.get("imageUrl", ""),
                        },
                    )
                )

            self._consecutive_failures = 0  # Reset on success
            return results[:max_results]
        except httpx.HTTPStatusError as e:
            import time as _time
            self._consecutive_failures += 1
            if self._consecutive_failures >= 3:
                self._circuit_open_until = _time.time() + 300  # 5-minute cooldown
                logger.warning("serper_circuit_breaker_open", failures=self._consecutive_failures, cooldown_s=300)
            logger.warning("serper_search_error", error=str(e), query=truncated_query[:100], status=e.response.status_code)
            return []
        except Exception as e:
            self._consecutive_failures += 1
            import time as _time
            if self._consecutive_failures >= 3:
                self._circuit_open_until = _time.time() + 300
            logger.warning("serper_search_error", error=str(e), query=truncated_query[:100])
            return []


# ------------------------------------------------------------------
# Academic: Semantic Scholar
# ------------------------------------------------------------------


class SemanticScholarEngine(SearchEngine):
    """Semantic Scholar — free academic paper search."""

    name = "semantic_scholar"
    _API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "query": query,
            "limit": min(max_results, 20),
            "fields": "title,url,abstract,year,citationCount,authors,publicationDate",
        }

        # Year filter
        if date_filter:
            now_year = datetime.now(timezone.utc).year
            year_map = {"day": now_year, "week": now_year, "month": now_year, "year": now_year - 1}
            year = year_map.get(date_filter, now_year - 5)
            params["year"] = f"{year}-"

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    self._API_URL,
                    params=params,
                    headers={"User-Agent": "MekongAI-Nexus/1.0"},
                )
                resp.raise_for_status()
                data = resp.json()

            papers = data.get("data", [])
            results: list[SearchResult] = []
            for p in papers[:max_results]:
                authors = ", ".join(
                    a.get("name", "") for a in (p.get("authors") or [])[:3]
                )
                results.append(
                    SearchResult(
                        title=p.get("title", ""),
                        url=p.get("url", f"https://api.semanticscholar.org/paper/{p.get('paperId', '')}"),
                        snippet=p.get("abstract", "")[:500] or f"By {authors} ({p.get('year', 'N/A')})",
                        source_engine=self.name,
                        published_date=p.get("publicationDate"),
                        metadata={
                            "year": p.get("year"),
                            "citation_count": p.get("citationCount", 0),
                            "authors": authors,
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("semantic_scholar_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Academic: arXiv
# ------------------------------------------------------------------


class ArxivEngine(SearchEngine):
    """arXiv — free academic preprint search via Atom API."""

    name = "arxiv"
    _API_URL = "http://export.arxiv.org/api/query"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": min(max_results, 20),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(self._API_URL, params=params)
                resp.raise_for_status()
                text = resp.text

            # Simple XML parsing (avoid heavy xml dependency)
            results: list[SearchResult] = []
            entries = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)

            for entry in entries[:max_results]:
                title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
                summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
                link_m = re.search(r'<id>(.*?)</id>', entry)
                published_m = re.search(r"<published>(.*?)</published>", entry)
                authors = re.findall(r"<name>(.*?)</name>", entry)

                title = title_m.group(1).strip() if title_m else ""
                summary = summary_m.group(1).strip() if summary_m else ""
                url = link_m.group(1).strip() if link_m else ""
                published = published_m.group(1).strip() if published_m else None

                # Clean up whitespace in title/summary
                title = re.sub(r"\s+", " ", title)
                summary = re.sub(r"\s+", " ", summary)

                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=summary[:500],
                        source_engine=self.name,
                        published_date=published,
                        metadata={
                            "authors": ", ".join(authors[:3]),
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("arxiv_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Knowledge: Wikipedia
# ------------------------------------------------------------------


class WikipediaEngine(SearchEngine):
    """Wikipedia search via MediaWiki API (free, no key)."""

    name = "wikipedia"
    _API_URL = "https://{lang}.wikipedia.org/w/api.php"

    # Map common country/region codes to Wikipedia language codes
    _REGION_TO_LANG: dict[str, str] = {
        "vn": "vi", "jp": "ja", "kr": "ko", "cn": "zh",
        "tw": "zh", "br": "pt", "us": "en", "gb": "en",
        "uk": "en", "de": "de", "fr": "fr", "es": "es",
        "it": "it", "ru": "ru", "in": "hi", "th": "th",
    }

    # Valid Wikipedia language codes (major languages with active wikis)
    _VALID_LANGS: set[str] = {
        "en", "vi", "ja", "ko", "zh", "pt", "de", "fr", "es",
        "it", "ru", "hi", "th", "ar", "nl", "pl", "sv", "uk",
        "id", "cs", "fi", "no", "tr", "da", "he", "el", "hu",
        "ro", "ca", "bg", "sr", "sk", "ms", "hr", "lt", "sl",
    }

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        # Convert region to valid Wikipedia language prefix
        lang = "en"
        if region and len(region) >= 2:
            candidate = self._REGION_TO_LANG.get(region.lower()[:2], region.lower()[:2])
            lang = candidate if candidate in self._VALID_LANGS else "en"
        api_url = self._API_URL.format(lang=lang)

        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": min(max_results, 20),
            "srprop": "snippet|timestamp|titlesnippet",
            "format": "json",
            "utf8": 1,
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as http:
                resp = await http.get(
                    api_url,
                    params=params,
                    headers={"User-Agent": "MekongAI-Nexus/1.0 (search engine)"},
                )
                resp.raise_for_status()
                data = resp.json()

            search_results = data.get("query", {}).get("search", [])
            results: list[SearchResult] = []

            for r in search_results[:max_results]:
                title = r.get("title", "")
                snippet_html = r.get("snippet", "")
                # Strip HTML tags from snippet
                snippet = re.sub(r"<.*?>", "", snippet_html)
                timestamp = r.get("timestamp")
                page_url = f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"

                results.append(
                    SearchResult(
                        title=title,
                        url=page_url,
                        snippet=snippet,
                        source_engine=self.name,
                        published_date=timestamp,
                        metadata={"language": lang, "wordcount": r.get("wordcount", 0)},
                    )
                )
            return results
        except Exception as e:
            logger.warning("wikipedia_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Knowledge: Wikidata
# ------------------------------------------------------------------


class WikidataEngine(SearchEngine):
    """Wikidata entity search via REST API (free, no key)."""

    name = "wikidata"
    _API_URL = "https://www.wikidata.org/w/api.php"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        # Reuse WikipediaEngine region mapping with strict validation
        lang = "en"
        if region and len(region) >= 2:
            candidate = WikipediaEngine._REGION_TO_LANG.get(region.lower()[:2], region.lower()[:2])
            lang = candidate if candidate in WikipediaEngine._VALID_LANGS else "en"

        params = {
            "action": "wbsearchentities",
            "search": query,
            "language": lang,
            "limit": min(max_results, 20),
            "format": "json",
            "uselang": lang,
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as http:
                resp = await http.get(
                    self._API_URL,
                    params=params,
                    headers={"User-Agent": "MekongAI-Nexus/1.0 (search engine)"},
                )
                resp.raise_for_status()
                data = resp.json()

            entities = data.get("search", [])
            results: list[SearchResult] = []

            for entity in entities[:max_results]:
                entity_id = entity.get("id", "")
                label = entity.get("label", "")
                description = entity.get("description", "")
                concept_uri = entity.get("concepturi", "")
                url = concept_uri or f"https://www.wikidata.org/wiki/{entity_id}"

                results.append(
                    SearchResult(
                        title=f"{label} ({entity_id})",
                        url=url,
                        snippet=description,
                        source_engine=self.name,
                        published_date=None,
                        metadata={
                            "entity_id": entity_id,
                            "label": label,
                            "language": lang,
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("wikidata_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Medical: PubMed
# ------------------------------------------------------------------


class PubMedEngine(SearchEngine):
    """PubMed search via NCBI E-utilities (free, no key required).

    Rate limit: 3 requests/second without API key.
    """

    name = "pubmed"
    _SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    _SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        # Step 1: Search for PMIDs
        search_params: dict[str, Any] = {
            "db": "pubmed",
            "term": query,
            "retmax": min(max_results, 20),
            "retmode": "json",
            "sort": "relevance",
        }

        # Date filter
        if date_filter:
            days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
            days = days_map.get(date_filter)
            if days:
                search_params["datetype"] = "edat"
                search_params["reldate"] = days

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    self._SEARCH_URL,
                    params=search_params,
                    headers={"User-Agent": "MekongAI-Nexus/1.0"},
                )
                resp.raise_for_status()
                search_data = resp.json()

            id_list = search_data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return []

            # Rate limit compliance
            await asyncio.sleep(0.35)

            # Step 2: Get summaries for PMIDs
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    self._SUMMARY_URL,
                    params={
                        "db": "pubmed",
                        "id": ",".join(id_list),
                        "retmode": "json",
                    },
                    headers={"User-Agent": "MekongAI-Nexus/1.0"},
                )
                resp.raise_for_status()
                summary_data = resp.json()

            result_map = summary_data.get("result", {})
            results: list[SearchResult] = []

            for pmid in id_list[:max_results]:
                article = result_map.get(pmid, {})
                if not article or pmid == "uids":
                    continue

                title = article.get("title", "")
                authors_list = article.get("authors", [])
                authors = ", ".join(
                    a.get("name", "") for a in authors_list[:3]
                )
                journal = article.get("fulljournalname", "") or article.get("source", "")
                pub_date = article.get("pubdate", "")
                url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

                snippet = f"By {authors}." if authors else ""
                if journal:
                    snippet += f" Published in {journal}."
                if pub_date:
                    snippet += f" ({pub_date})"

                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        source_engine=self.name,
                        published_date=pub_date or None,
                        metadata={
                            "pmid": pmid,
                            "authors": authors,
                            "journal": journal,
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("pubmed_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Open Access: CORE
# ------------------------------------------------------------------


class COREEngine(SearchEngine):
    """CORE — open access research papers (free, no key for basic search).

    API v3: https://api.core.ac.uk/v3/
    300M+ open access papers with full-text.
    """

    name = "core"
    _API_URL = "https://api.core.ac.uk/v3/search/works"

    def __init__(self) -> None:
        self._api_key = os.environ.get("CORE_API_KEY", "")

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "q": query,
            "limit": min(max_results, 20),
        }

        # Date filter via year range
        if date_filter:
            now_year = datetime.now(timezone.utc).year
            year_map = {"day": now_year, "week": now_year, "month": now_year, "year": now_year - 1}
            year = year_map.get(date_filter, now_year - 5)
            params["q"] = f"{query} AND yearPublished>={year}"

        headers: dict[str, str] = {"User-Agent": "MekongAI-Nexus/1.0"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    self._API_URL,
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            works = data.get("results", [])
            results: list[SearchResult] = []

            for work in works[:max_results]:
                title = work.get("title", "")
                abstract = work.get("abstract", "") or ""
                authors_list = work.get("authors", [])
                authors = ", ".join(
                    a.get("name", "") for a in (authors_list or [])[:3]
                )
                pub_date = work.get("publishedDate") or work.get("yearPublished")
                if pub_date and isinstance(pub_date, int):
                    pub_date = str(pub_date)

                # Prefer DOI URL, then downloadUrl, then CORE URL
                doi = work.get("doi", "")
                download_url = work.get("downloadUrl", "")
                core_id = work.get("id", "")
                url = (
                    f"https://doi.org/{doi}" if doi
                    else download_url or f"https://core.ac.uk/works/{core_id}"
                )

                snippet = abstract[:500] if abstract else f"By {authors}" if authors else ""

                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        source_engine=self.name,
                        published_date=pub_date,
                        metadata={
                            "authors": authors,
                            "doi": doi,
                            "core_id": core_id,
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("core_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Code: GitHub
# ------------------------------------------------------------------


class GitHubSearchEngine(SearchEngine):
    """GitHub repository search (free, optional GITHUB_TOKEN for higher rate).

    Rate limit: 10 req/min (unauthenticated), 30 req/min (authenticated).
    """

    name = "github"
    _API_URL = "https://api.github.com/search/repositories"

    def __init__(self) -> None:
        self._token = os.environ.get("GITHUB_TOKEN", "")

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        params: dict[str, Any] = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max_results, 30),
        }

        # Date filter: pushed after date
        if date_filter:
            now = datetime.now(timezone.utc)
            days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
            days = days_map.get(date_filter)
            if days:
                since = (now - timedelta(days=days)).strftime("%Y-%m-%d")
                params["q"] = f"{query} pushed:>{since}"

        headers: dict[str, str] = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "MekongAI-Nexus/1.0",
        }
        if self._token:
            headers["Authorization"] = f"token {self._token}"

        try:
            async with httpx.AsyncClient(timeout=12.0) as http:
                resp = await http.get(
                    self._API_URL,
                    params=params,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()

            items = data.get("items", [])
            results: list[SearchResult] = []

            for repo in items[:max_results]:
                full_name = repo.get("full_name", "")
                description = repo.get("description", "") or ""
                html_url = repo.get("html_url", "")
                stars = repo.get("stargazers_count", 0)
                language = repo.get("language", "")
                updated_at = repo.get("updated_at")
                topics = repo.get("topics", [])

                snippet = description[:300]
                if language:
                    snippet += f" [{language}]"
                if stars:
                    snippet += f" ⭐ {stars}"

                results.append(
                    SearchResult(
                        title=full_name,
                        url=html_url,
                        snippet=snippet,
                        source_engine=self.name,
                        published_date=updated_at,
                        metadata={
                            "stars": stars,
                            "language": language,
                            "topics": topics[:5],
                        },
                    )
                )
            return results
        except Exception as e:
            logger.warning("github_search_error", error=str(e), query=query[:100])
            return []


# ------------------------------------------------------------------
# Security Research Engines (free, no API key)
# ------------------------------------------------------------------


class ExploitDBEngine(SearchEngine):
    """Exploit-DB search via their JSON API (no API key needed).

    Searches for exploits, PoCs, and vulnerability details.
    Uses the DataTables AJAX endpoint for reliable JSON responses.
    """

    name = "exploitdb"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
                resp = await http.get(
                    "https://www.exploit-db.com/search",
                    params={
                        "q": query,
                        "type": "exploits",
                        "draw": "1",
                        "columns[0][data]": "id",
                    },
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                        "X-Requested-With": "XMLHttpRequest",
                        "Accept": "application/json",
                    },
                )
                if resp.status_code != 200:
                    return []

                data = resp.json()
                results: list[SearchResult] = []
                for entry in data.get("data", [])[:max_results]:
                    exploit_id = str(entry.get("id", ""))
                    desc_raw = entry.get("description", [])
                    title = desc_raw[1] if isinstance(desc_raw, list) and len(desc_raw) > 1 else str(desc_raw)
                    platform = entry.get("platform_id", "")
                    date_pub = entry.get("date_published", "")
                    author_raw = entry.get("author_id", [])
                    author = author_raw[1] if isinstance(author_raw, list) and len(author_raw) > 1 else ""

                    results.append(SearchResult(
                        title=title,
                        url=f"https://www.exploit-db.com/exploits/{exploit_id}",
                        snippet=f"[{platform}] by {author} ({date_pub})" if author else f"[{platform}] ({date_pub})",
                        source_engine=self.name,
                        published_date=date_pub,
                        metadata={"exploit_id": exploit_id, "platform": platform},
                    ))
                return results
        except Exception as e:
            logger.warning("exploitdb_search_error", error=str(e))
            return []


class NVDEngine(SearchEngine):
    """NIST NVD (National Vulnerability Database) search — free public API.

    Rate limit: 5 requests per 30 seconds (no key), 50/30s (with key).
    """

    name = "nvd"
    _API_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        try:
            params: dict[str, Any] = {"keywordSearch": query, "resultsPerPage": max_results}
            headers = {"User-Agent": "MekongAI-Nexus/1.0"}

            # If query looks like a CVE ID, use exact search
            import re as _re
            cve_match = _re.search(r"CVE-\d{4}-\d+", query)
            if cve_match:
                params = {"cveId": cve_match.group(0)}

            async with httpx.AsyncClient(timeout=20.0) as http:
                resp = await http.get(self._API_URL, params=params, headers=headers)
                if resp.status_code != 200:
                    return []
                data = resp.json()

            results: list[SearchResult] = []
            for vuln in data.get("vulnerabilities", [])[:max_results]:
                cve = vuln.get("cve", {})
                cve_id = cve.get("id", "")
                descriptions = cve.get("descriptions", [])
                desc = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")
                metrics = cve.get("metrics", {})
                cvss_score = ""
                for v in metrics.get("cvssMetricV31", []):
                    cvss_score = str(v.get("cvssData", {}).get("baseScore", ""))
                    break

                results.append(SearchResult(
                    title=f"{cve_id} — {desc[:80]}",
                    url=f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                    snippet=desc[:300] + (f" [CVSS: {cvss_score}]" if cvss_score else ""),
                    source_engine=self.name,
                    published_date=cve.get("published"),
                    metadata={"cvss": cvss_score, "cve_id": cve_id},
                ))
            return results
        except Exception as e:
            logger.warning("nvd_search_error", error=str(e))
            return []


class GoogleWebEngine(SearchEngine):
    """Google web search via HTML scraping (free, no API key, no LLM needed).

    Scrapes Google search results directly. May get rate-limited or CAPTCHAed
    if overused — use as secondary engine after DuckDuckGo.
    """

    name = "google_web"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        try:
            import re as _re
            params = {"q": query, "num": min(max_results + 5, 20), "hl": "en"}
            if date_filter:
                tbs_map = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
                if date_filter in tbs_map:
                    params["tbs"] = tbs_map[date_filter]

            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as http:
                resp = await http.get(
                    "https://www.google.com/search",
                    params=params,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                if resp.status_code != 200:
                    return []

                results: list[SearchResult] = []
                # Extract search result blocks
                blocks = _re.findall(
                    r'<a[^>]*href="(/url\?q=([^&"]+)[^"]*)"[^>]*>(.*?)</a>',
                    resp.text, _re.DOTALL,
                )
                seen_urls: set[str] = set()
                for _, raw_url, title_html in blocks:
                    url = unquote(raw_url)
                    if not url.startswith("http") or url in seen_urls:
                        continue
                    if any(skip in url for skip in ("google.com", "accounts.google", "support.google")):
                        continue
                    seen_urls.add(url)
                    title = _re.sub(r"<[^>]+>", "", title_html).strip()
                    if not title or len(title) < 5:
                        continue

                    # Try to extract snippet from nearby text
                    snippet = ""
                    snippet_match = _re.search(
                        rf'{_re.escape(url)}.*?<span[^>]*>(.*?)</span>',
                        resp.text[resp.text.find(url):resp.text.find(url)+2000],
                        _re.DOTALL,
                    )
                    if snippet_match:
                        snippet = _re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()[:200]

                    results.append(SearchResult(
                        title=title[:200],
                        url=url,
                        snippet=snippet or title,
                        source_engine=self.name,
                    ))
                    if len(results) >= max_results:
                        break
                return results
        except Exception as e:
            logger.warning("google_web_error", error=str(e))
            return []


class GoogleScholarEngine(SearchEngine):
    """Google Scholar scraping (free, no API key). Useful for security papers."""

    name = "google_scholar"

    async def search(
        self,
        query: str,
        max_results: int = 5,
        *,
        date_filter: Optional[str] = None,
        region: Optional[str] = None,
        **kwargs: Any,
    ) -> list[SearchResult]:
        try:
            import re as _re
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
                resp = await http.get(
                    "https://scholar.google.com/scholar",
                    params={"q": query, "num": max_results, "hl": "en"},
                    headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    },
                )
                if resp.status_code != 200:
                    return []

                results: list[SearchResult] = []
                entries = _re.findall(
                    r'<h3[^>]*class="gs_rt"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                    resp.text, _re.DOTALL,
                )
                for url, title_html in entries[:max_results]:
                    title = _re.sub(r"<[^>]+>", "", title_html).strip()
                    results.append(SearchResult(
                        title=title,
                        url=url,
                        snippet=f"Google Scholar: {title}",
                        source_engine=self.name,
                    ))
                return results
        except Exception as e:
            logger.warning("google_scholar_error", error=str(e))
            return []


# ------------------------------------------------------------------
# Engine Registry — singleton for discovering available engines
# ------------------------------------------------------------------


_ENGINE_INSTANCES: dict[str, SearchEngine] = {}


def get_available_engines() -> dict[str, SearchEngine]:
    """Return dict of instantiated, available search engines.

    Engines are lazily created and cached. Unavailable engines
    (missing API keys) are excluded.
    """
    global _ENGINE_INSTANCES
    if not _ENGINE_INSTANCES:
        _all_engines: list[SearchEngine] = [
            DuckDuckGoEngine(),
            DuckDuckGoNewsEngine(),
            BraveSearchEngine(),
            TavilySearchEngine(),
            SerperSearchEngine(),
            SemanticScholarEngine(),
            ArxivEngine(),
            # Free engines (no API key required)
            WikipediaEngine(),
            WikidataEngine(),
            PubMedEngine(),
            COREEngine(),
            GitHubSearchEngine(),
            # Security research engines (free)
            ExploitDBEngine(),
            NVDEngine(),
            GoogleScholarEngine(),
            GoogleWebEngine(),
        ]
        _ENGINE_INSTANCES = {e.name: e for e in _all_engines if e.is_available()}

    return _ENGINE_INSTANCES


def get_engine(name: str) -> Optional[SearchEngine]:
    """Get a specific engine by name, or None if unavailable."""
    return get_available_engines().get(name)
