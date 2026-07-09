"""
MekongAI Nexus - Web Tools.

Register web_search, web_search_news, web_search_academic, fetch_url
into the global tool registry.

Enhanced with multi-engine search orchestrator (Phase 1):
- DuckDuckGo (always available, no key)
- Brave Search (BRAVE_API_KEY)
- Tavily AI Search (TAVILY_API_KEY)
- Serper / Google (SERPER_API_KEY)
- Semantic Scholar + arXiv (academic, free)

Backward compatible: without API keys, falls back to DuckDuckGo only.
"""

import re
from typing import Any, Optional

import httpx
import structlog

from app.core.command_guard import check_url_safety
from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

# Shared HTTP client (lazy)
_http_client: Optional[httpx.AsyncClient] = None


async def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    return _http_client


# ------------------------------------------------------------------
# Format helpers
# ------------------------------------------------------------------


def _format_search_results(response: Any) -> str:
    """Format SearchResponse into compact output."""
    if not response.results:
        return "No results found."

    lines: list[str] = []

    for i, r in enumerate(response.results, 1):
        lines.append(f"{i}. {r.title}")
        lines.append(f"   {r.url}")
        if r.snippet:
            lines.append(f"   {r.snippet[:150]}")
        lines.append("")

    # Search result titles/snippets are third-party content — fence as data.
    from app.core.untrusted_content import wrap_untrusted

    return wrap_untrusted("\n".join(lines), source="web search")


def _results_to_data(response: Any) -> list[dict[str, Any]]:
    """Convert SearchResponse results to serializable dicts."""
    return [
        {
            "title": r.title,
            "url": r.url,
            "snippet": r.snippet,
            "source_engine": r.source_engine,
            "published_date": r.published_date,
            "domain": r.domain,
            "score": r.raw_score,
        }
        for r in response.results
    ]


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_web_search(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Multi-engine web search with freshness scoring.

    Enhanced from single DuckDuckGo to multi-engine orchestration.
    Backward compatible: old params (query, max_results) still work.
    """
    query = params.get("query", "")
    max_results = params.get("max_results", 10)
    mode = params.get("mode", "general")
    date_filter = params.get("date_filter", None)
    sort_by = params.get("sort_by", "relevance")
    region = params.get("region", None)
    engines = params.get("engines", None)

    if not query:
        return {"success": False, "output": "No query", "data": None, "artifacts": []}

    try:
        from app.mcp.tools.search_orchestrator import get_search_orchestrator

        orchestrator = get_search_orchestrator()
        response = await orchestrator.search(
            query=query,
            mode=mode,
            max_results=max_results,
            date_filter=date_filter,
            region=region,
            engines=engines,
            sort_by=sort_by,
        )

        output = _format_search_results(response)
        data = _results_to_data(response)

        # SEARCH-FIX: Report failure when no results returned despite execution.
        # Previously always returned success=True even with 0 results from all
        # engines failing. This caused agents to believe research succeeded and
        # produce shallow empty arguments → empty final synthesis.
        has_results = bool(data) and len(data) > 0
        _no_result_hint = (
            f"Search returned 0 results for: {query}. "
            f"Engines failed: {response.engines_failed}. "
            "OPTIONS: (1) Try different keywords, (2) Use browser_ai_action tool to "
            "search Google/X/GitHub directly in a real browser — it finds content that "
            "API-based search cannot reach."
        )
        return {
            "success": has_results,
            "output": output if has_results else _no_result_hint,
            "data": data,
            "artifacts": [],
            "metadata": {
                "engines_used": response.engines_used,
                "engines_failed": response.engines_failed,
                "search_time_ms": response.search_time_ms,
                "mode": response.mode,
                "result_count": len(data) if data else 0,
            },
        }
    except Exception as e:
        logger.error("web_search_orchestrator_error", error=str(e), query=query[:100])
        # Fallback to legacy single-engine search
        return await _legacy_web_search(params)


async def _legacy_web_search(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Legacy fallback: single DuckDuckGo search (if orchestrator fails)."""
    query = params.get("query", "")
    max_results = params.get("max_results", 5)

    try:
        from ddgs import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })

        output = "\n\n".join(
            f"**{r['title']}**\n{r['url']}\n{r['snippet']}" for r in results
        )
        return {
            "success": True,
            "output": output or "No results found",
            "data": results,
            "artifacts": [],
        }
    except Exception as e:
        logger.warning("legacy_search_failed", error=str(e))
        return {"success": False, "output": f"Search failed: {e}", "data": None, "artifacts": []}


async def handle_web_search_news(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Search for recent news articles across multiple sources.

    Optimized for finding the latest news with freshness-weighted scoring.
    """
    query = params.get("query", "")
    max_results = params.get("max_results", 10)
    timeframe = params.get("timeframe", "week")
    region = params.get("region", None)

    if not query:
        return {"success": False, "output": "No query", "data": None, "artifacts": []}

    # Map timeframe to date_filter
    timeframe_map = {"24h": "day", "7d": "week", "30d": "month"}
    date_filter = timeframe_map.get(timeframe, timeframe)

    try:
        from app.mcp.tools.search_orchestrator import get_search_orchestrator

        orchestrator = get_search_orchestrator()
        response = await orchestrator.search(
            query=query,
            mode="news",
            max_results=max_results,
            date_filter=date_filter,
            region=region,
            sort_by="freshness",
        )

        output = _format_search_results(response)

        return {
            "success": True,
            "output": f"📰 **News results for '{query}':**\n\n{output}" if output else "No news found",
            "data": _results_to_data(response),
            "artifacts": [],
            "metadata": {
                "engines_used": response.engines_used,
                "search_time_ms": response.search_time_ms,
            },
        }
    except Exception as e:
        logger.error("news_search_error", error=str(e))
        return {"success": False, "output": f"News search failed: {e}", "data": None, "artifacts": []}


async def handle_web_search_academic(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Search academic papers and research articles.

    Uses Semantic Scholar and arXiv APIs (both free, no key required).
    """
    query = params.get("query", "")
    max_results = params.get("max_results", 10)
    date_filter = params.get("date_filter", None)

    if not query:
        return {"success": False, "output": "No query", "data": None, "artifacts": []}

    try:
        from app.mcp.tools.search_orchestrator import get_search_orchestrator

        orchestrator = get_search_orchestrator()
        response = await orchestrator.search(
            query=query,
            mode="academic",
            max_results=max_results,
            date_filter=date_filter,
        )

        # Format academic results with extra metadata
        lines: list[str] = []
        for i, r in enumerate(response.results, 1):
            authors = r.metadata.get("authors", "")
            year = r.metadata.get("year", "")
            citations = r.metadata.get("citation_count", "")

            lines.append(f"**{i}. {r.title}**")
            if authors:
                lines.append(f"   Authors: {authors}")
            if year:
                cite_str = f" | Citations: {citations}" if citations else ""
                lines.append(f"   Year: {year}{cite_str}")
            lines.append(f"   {r.url}")
            if r.snippet:
                lines.append(f"   {r.snippet[:300]}")
            lines.append("")

        output = "\n".join(lines)

        return {
            "success": True,
            "output": f"📚 **Academic results for '{query}':**\n\n{output}" if output else "No papers found",
            "data": _results_to_data(response),
            "artifacts": [],
            "metadata": {
                "engines_used": response.engines_used,
                "search_time_ms": response.search_time_ms,
            },
        }
    except Exception as e:
        logger.error("academic_search_error", error=str(e))
        return {"success": False, "output": f"Academic search failed: {e}", "data": None, "artifacts": []}


async def handle_web_search_knowledge(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Search knowledge bases (Wikipedia, Wikidata) for facts and definitions.

    Returns structured knowledge with entity IDs, descriptions, and links.
    """
    query = params.get("query", "")
    max_results = params.get("max_results", 10)
    language = params.get("language", "en")

    if not query:
        return {"success": False, "output": "No query", "data": None, "artifacts": []}

    try:
        from app.mcp.tools.search_orchestrator import get_search_orchestrator

        orchestrator = get_search_orchestrator()
        response = await orchestrator.search(
            query=query,
            mode="knowledge",
            max_results=max_results,
            region=language,
        )

        # Format knowledge results with entity metadata
        lines: list[str] = []
        for i, r in enumerate(response.results, 1):
            entity_id = r.metadata.get("entity_id", "")
            wordcount = r.metadata.get("wordcount", "")

            lines.append(f"**{i}. {r.title}**")
            if entity_id:
                lines.append(f"   Entity: {entity_id}")
            lines.append(f"   {r.url}")
            if r.snippet:
                lines.append(f"   {r.snippet[:400]}")
            if wordcount:
                lines.append(f"   (Article length: {wordcount} words)")
            lines.append("")

        output = "\n".join(lines)

        return {
            "success": True,
            "output": f"📖 **Knowledge results for '{query}':**\n\n{output}" if output else "No results found",
            "data": _results_to_data(response),
            "artifacts": [],
            "metadata": {
                "engines_used": response.engines_used,
                "search_time_ms": response.search_time_ms,
            },
        }
    except Exception as e:
        logger.error("knowledge_search_error", error=str(e))
        return {"success": False, "output": f"Knowledge search failed: {e}", "data": None, "artifacts": []}


async def handle_web_search_medical(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Search biomedical literature via PubMed and Semantic Scholar.

    Returns papers with authors, journal, PubMed IDs, and publication dates.
    """
    query = params.get("query", "")
    max_results = params.get("max_results", 10)
    date_filter = params.get("date_filter", None)

    if not query:
        return {"success": False, "output": "No query", "data": None, "artifacts": []}

    try:
        from app.mcp.tools.search_orchestrator import get_search_orchestrator

        orchestrator = get_search_orchestrator()
        response = await orchestrator.search(
            query=query,
            mode="medical",
            max_results=max_results,
            date_filter=date_filter,
        )

        # Format medical results with PubMed metadata
        lines: list[str] = []
        for i, r in enumerate(response.results, 1):
            pmid = r.metadata.get("pmid", "")
            journal = r.metadata.get("journal", "")
            authors = r.metadata.get("authors", "")

            lines.append(f"**{i}. {r.title}**")
            if authors:
                lines.append(f"   Authors: {authors}")
            if journal:
                lines.append(f"   Journal: {journal}")
            if pmid:
                lines.append(f"   PMID: {pmid}")
            lines.append(f"   {r.url}")
            if r.snippet:
                lines.append(f"   {r.snippet[:300]}")
            lines.append("")

        output = "\n".join(lines)

        return {
            "success": True,
            "output": f"🏥 **Medical results for '{query}':**\n\n{output}" if output else "No papers found",
            "data": _results_to_data(response),
            "artifacts": [],
            "metadata": {
                "engines_used": response.engines_used,
                "search_time_ms": response.search_time_ms,
            },
        }
    except Exception as e:
        logger.error("medical_search_error", error=str(e))
        return {"success": False, "output": f"Medical search failed: {e}", "data": None, "artifacts": []}


async def handle_fetch_url(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Fetch nội dung từ URL (web scraping) với SSRF protection."""
    url = params.get("url", "")
    if not url:
        return {"success": False, "output": "No URL", "data": None, "artifacts": []}

    url_verdict = check_url_safety(url)
    if not url_verdict.safe:
        logger.warning("ssrf_blocked", url=url[:200], reason=url_verdict.reason)
        return {
            "success": False,
            "output": f"⚠️ URL blocked (SSRF protection): {url_verdict.reason}",
            "data": {"blocked": True, "reason": url_verdict.reason},
            "artifacts": [],
        }

    try:
        http = await _get_http()
        resp = await http.get(url, headers={"User-Agent": "MekongAI-Nexus/1.0"})
        content = resp.text[:50000]

        clean = re.sub(r'<script.*?</script>', '', content, flags=re.DOTALL)
        clean = re.sub(r'<style.*?</style>', '', clean, flags=re.DOTALL)
        clean = re.sub(r'<.*?>', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean).strip()

        # Fetched page body is untrusted — fence as data to resist prompt injection.
        from app.core.untrusted_content import wrap_untrusted

        return {
            "success": True,
            "output": wrap_untrusted(clean[:10000], source="web"),
            "data": {"url": url, "status": resp.status_code, "length": len(clean)},
            "artifacts": [],
        }
    except Exception as e:
        return {"success": False, "output": f"Fetch failed: {e}", "data": None, "artifacts": []}


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "web"

registry.register(
    name="web_search",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Search the web for information using multiple search engines "
            "(DuckDuckGo, Brave, Tavily, Google/Serper, Wikipedia, PubMed, GitHub). "
            "Results are scored by freshness and source credibility. Supports date "
            "filtering and different search modes (general, news, academic, code, "
            "knowledge, medical)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 10, "description": "Max results (default: 10)"},
                "mode": {
                    "type": "string",
                    "enum": ["general", "news", "academic", "code", "knowledge", "medical", "security"],
                    "description": (
                        "Search mode (default: general). Use 'news' for recent events, "
                        "'academic' for papers, 'code' for programming, "
                        "'knowledge' for Wikipedia/Wikidata facts, 'medical' for PubMed/biomedical, "
                        "'security' for CVEs/exploits/PoCs (searches NVD, Exploit-DB, GitHub)."
                    ),
                },
                "date_filter": {
                    "type": "string",
                    "enum": ["day", "week", "month", "year"],
                    "description": "Filter by time period. Use 'day' for last 24h, 'week' for last 7 days, etc.",
                },
                "sort_by": {
                    "type": "string",
                    "enum": ["relevance", "freshness"],
                    "description": "Sort results by combined relevance score or by freshness (most recent first).",
                },
                "region": {
                    "type": "string",
                    "description": "Region code (e.g., 'vn' for Vietnam, 'us' for US). Affects result locality.",
                },
                "engines": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Explicit engines to use: 'duckduckgo', 'google_web', 'brave', 'tavily', "
                        "'serper', 'wikipedia', 'wikidata', 'pubmed', 'core', 'github', "
                        "'semantic_scholar', 'arxiv', 'nvd', 'exploitdb', 'google_scholar'. "
                        "If omitted, auto-selected by mode."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    handler=handle_web_search,
    is_async=True,
    emoji="🔍",
)

registry.register(
    name="web_search_news",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Search for recent news articles. Optimized for finding the latest "
            "news with freshness-weighted scoring. Uses multiple news sources "
            "for comprehensive coverage."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "News search query"},
                "max_results": {"type": "integer", "default": 10},
                "timeframe": {
                    "type": "string",
                    "enum": ["24h", "7d", "30d"],
                    "description": "How recent: '24h' for today, '7d' for this week, '30d' for this month",
                },
                "region": {
                    "type": "string",
                    "description": "Region code for local news (e.g., 'vn', 'us')",
                },
            },
            "required": ["query"],
        },
    },
    handler=handle_web_search_news,
    is_async=True,
    emoji="📰",
)

registry.register(
    name="web_search_academic",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Search academic papers and research articles via Semantic Scholar "
            "and arXiv (both free, no API key required). Returns papers with "
            "authors, year, citation count, and abstracts."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Academic search query (paper titles, topics, authors)"},
                "max_results": {"type": "integer", "default": 10},
                "date_filter": {
                    "type": "string",
                    "enum": ["week", "month", "year"],
                    "description": "Filter by publication recency",
                },
            },
            "required": ["query"],
        },
    },
    handler=handle_web_search_academic,
    is_async=True,
    emoji="📚",
)

registry.register(
    name="web_search_knowledge",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Search knowledge bases (Wikipedia, Wikidata) for facts, definitions, "
            "and structured data. Free, no API key required. Use for factual lookups, "
            "entity information, and encyclopedic content."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Knowledge search query (topics, entities, definitions)"},
                "max_results": {"type": "integer", "default": 10},
                "language": {
                    "type": "string",
                    "description": "Language code (e.g., 'en', 'vi', 'fr'). Default: 'en'",
                },
            },
            "required": ["query"],
        },
    },
    handler=handle_web_search_knowledge,
    is_async=True,
    emoji="📖",
)

registry.register(
    name="web_search_medical",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Search biomedical literature via PubMed and Semantic Scholar. "
            "Free, no API key required. Returns papers with authors, journal, "
            "PubMed IDs, and citations. Use for health, medicine, and biology topics."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Medical/biomedical search query"},
                "max_results": {"type": "integer", "default": 10},
                "date_filter": {
                    "type": "string",
                    "enum": ["week", "month", "year"],
                    "description": "Filter by publication recency",
                },
            },
            "required": ["query"],
        },
    },
    handler=handle_web_search_medical,
    is_async=True,
    emoji="🏥",
)

# FIX: GAP-1 - Enhanced fetch_url description
registry.register(
    name="fetch_url",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Fetch and extract readable text content from a URL. Strips HTML tags, scripts, "
            "and styles to return clean text. Use when you need to read a specific webpage, "
            "documentation page, or API response. For large pages, content is truncated to 10K chars. "
            "Protected against SSRF — internal/private IPs are blocked."
        ),
        "parameters": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "URL to fetch"}},
            "required": ["url"],
        },
    },
    handler=handle_fetch_url,
    is_async=True,
    emoji="🌐",
)
