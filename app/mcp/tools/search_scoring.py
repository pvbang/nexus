"""
Nexus - Search Result Scoring.

Score search results based on:
- Freshness: How recent is the content?
- Credibility: How trustworthy is the source domain?
- Relevance: Engine-provided relevance signals

Combined scoring allows intelligent sorting of multi-engine results.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Optional
from urllib.parse import urlparse

import structlog

from app.mcp.tools.search_engines import SearchResult

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Credibility Database
# ------------------------------------------------------------------

# Domain suffix credibility scores (0.0 - 1.0)
_SUFFIX_SCORES: dict[str, float] = {
    ".gov": 0.95,
    ".edu": 0.90,
    ".org": 0.75,
    ".ac.uk": 0.90,
    ".ac.jp": 0.90,
    ".gov.vn": 0.90,
    ".edu.vn": 0.85,
}

# Known domain credibility scores
_DOMAIN_SCORES: dict[str, float] = {
    # Tier 1 — Highest trust (0.90+)
    "reuters.com": 0.95,
    "apnews.com": 0.95,
    "bbc.com": 0.93,
    "bbc.co.uk": 0.93,
    "nature.com": 0.95,
    "science.org": 0.95,
    "sciencedirect.com": 0.92,
    "pubmed.ncbi.nlm.nih.gov": 0.95,
    "ncbi.nlm.nih.gov": 0.93,
    "nih.gov": 0.95,
    "arxiv.org": 0.90,
    "ieee.org": 0.92,
    "acm.org": 0.92,
    "who.int": 0.95,
    "un.org": 0.93,
    "worldbank.org": 0.92,
    "nist.gov": 0.95,
    # Tier 2 — High trust (0.80+)
    "github.com": 0.85,
    "stackoverflow.com": 0.85,
    "docs.python.org": 0.90,
    "docs.microsoft.com": 0.88,
    "learn.microsoft.com": 0.88,
    "developer.mozilla.org": 0.90,
    "cloud.google.com": 0.88,
    "aws.amazon.com": 0.88,
    "wikipedia.org": 0.80,
    "en.wikipedia.org": 0.80,
    "vi.wikipedia.org": 0.78,
    "wikidata.org": 0.82,
    "core.ac.uk": 0.80,
    "nytimes.com": 0.85,
    "washingtonpost.com": 0.85,
    "theguardian.com": 0.83,
    "economist.com": 0.88,
    "bloomberg.com": 0.87,
    "cnbc.com": 0.83,
    "forbes.com": 0.80,
    "investopedia.com": 0.82,
    "vnexpress.net": 0.80,
    "tuoitre.vn": 0.78,
    "thanhnien.vn": 0.78,
    # Tier 3 — Medium trust (0.60+)
    "medium.com": 0.60,
    "dev.to": 0.65,
    "hackernews.com": 0.65,
    "news.ycombinator.com": 0.65,
    "reddit.com": 0.55,
    "quora.com": 0.55,
    "youtube.com": 0.60,
    "twitter.com": 0.50,
    "x.com": 0.50,
    "facebook.com": 0.45,
    "linkedin.com": 0.60,
    "tiktok.com": 0.40,
    # Tier 4 — Low trust (< 0.50)
    "pinterest.com": 0.35,
    "buzzfeed.com": 0.40,
}

_DEFAULT_CREDIBILITY = 0.50


# ------------------------------------------------------------------
# Freshness Scorer
# ------------------------------------------------------------------


class FreshnessScorer:
    """Score a search result based on how recent it is.

    Scoring curve:
        - Today: 1.0
        - This week: 0.85-0.95
        - This month: 0.70-0.85
        - This year: 0.40-0.70
        - Older: 0.10-0.40
        - Unknown date: 0.30 (neutral penalty)
    """

    @staticmethod
    def score(result: SearchResult) -> float:
        """Return freshness score 0.0 - 1.0."""
        pub_date = _parse_date(result.published_date)
        if pub_date is None:
            return 0.30  # Unknown date gets neutral score

        now = datetime.now(timezone.utc)
        age = now - pub_date

        if age < timedelta(hours=24):
            return 1.0
        elif age < timedelta(days=3):
            return 0.95
        elif age < timedelta(days=7):
            return 0.85
        elif age < timedelta(days=14):
            return 0.80
        elif age < timedelta(days=30):
            return 0.75
        elif age < timedelta(days=90):
            return 0.65
        elif age < timedelta(days=180):
            return 0.55
        elif age < timedelta(days=365):
            return 0.45
        elif age < timedelta(days=730):
            return 0.30
        else:
            return 0.15


# ------------------------------------------------------------------
# Credibility Scorer
# ------------------------------------------------------------------


class CredibilityScorer:
    """Score a search result based on source domain trustworthiness."""

    @staticmethod
    def score(result: SearchResult) -> float:
        """Return credibility score 0.0 - 1.0."""
        domain = result.domain.lower()
        if not domain:
            return _DEFAULT_CREDIBILITY

        # Strip www. prefix
        if domain.startswith("www."):
            domain = domain[4:]

        # Check exact domain match first
        if domain in _DOMAIN_SCORES:
            return _DOMAIN_SCORES[domain]

        # Check parent domain (e.g., sub.example.com → example.com)
        parts = domain.split(".")
        if len(parts) > 2:
            parent = ".".join(parts[-2:])
            if parent in _DOMAIN_SCORES:
                return _DOMAIN_SCORES[parent]

        # Check domain suffix
        for suffix, score in _SUFFIX_SCORES.items():
            if domain.endswith(suffix):
                return score

        return _DEFAULT_CREDIBILITY


# ------------------------------------------------------------------
# Combined Scorer
# ------------------------------------------------------------------


class ResultScorer:
    """Combined scorer that weights freshness, credibility, and relevance.

    Final score = w_fresh * freshness + w_cred * credibility + w_rel * relevance
    """

    def __init__(
        self,
        weight_freshness: float = 0.35,
        weight_credibility: float = 0.35,
        weight_relevance: float = 0.30,
    ) -> None:
        self.w_fresh = weight_freshness
        self.w_cred = weight_credibility
        self.w_rel = weight_relevance
        self._freshness = FreshnessScorer()
        self._credibility = CredibilityScorer()

    def score(self, result: SearchResult, position: int = 0) -> float:
        """Compute combined score for a search result.

        Args:
            result: The search result to score.
            position: Position in original engine results (0-based).
                      Lower position → higher relevance.
        """
        fresh = self._freshness.score(result)
        cred = self._credibility.score(result)

        # Relevance from engine score or position
        if result.raw_score > 0:
            rel = min(result.raw_score, 1.0)
        else:
            # Position-based: first result = 1.0, decays by 0.08 per position
            rel = max(0.2, 1.0 - position * 0.08)

        combined = (
            self.w_fresh * fresh + self.w_cred * cred + self.w_rel * rel
        )
        return round(combined, 4)

    def score_and_sort(
        self, results: list[SearchResult]
    ) -> list[tuple[SearchResult, float]]:
        """Score all results and return sorted by combined score (descending)."""
        scored: list[tuple[SearchResult, float]] = []
        for i, r in enumerate(results):
            s = self.score(r, position=i)
            scored.append((r, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


# ------------------------------------------------------------------
# Date parsing helpers
# ------------------------------------------------------------------

_DATE_PATTERNS = [
    # ISO 8601 variants
    (r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", "%Y-%m-%dT%H:%M:%S"),
    (r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"),
    # Common formats
    (r"(\d{1,2}\s+\w+\s+\d{4})", None),  # "15 January 2025" — handled separately
    (r"(\w+\s+\d{1,2},?\s+\d{4})", None),  # "January 15, 2025"
    # Relative time ("2 hours ago", "3 days ago")
    (r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", None),
]


def _parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """Best-effort parse a date string into a timezone-aware datetime."""
    if not date_str:
        return None

    date_str = date_str.strip()

    # Try ISO 8601 first
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(date_str[:26], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except (ValueError, IndexError):
            continue

    # Try relative time ("2 hours ago", "3 days ago")
    rel_match = re.match(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", date_str, re.IGNORECASE)
    if rel_match:
        amount = int(rel_match.group(1))
        unit = rel_match.group(2).lower()
        now = datetime.now(timezone.utc)
        delta_map = {
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "month": timedelta(days=amount * 30),
            "year": timedelta(days=amount * 365),
        }
        delta = delta_map.get(unit)
        if delta:
            return now - delta

    # Try common date formats
    for fmt in [
        "%B %d, %Y",       # January 15, 2025
        "%b %d, %Y",       # Jan 15, 2025
        "%d %B %Y",        # 15 January 2025
        "%d %b %Y",        # 15 Jan 2025
        "%m/%d/%Y",        # 01/15/2025
        "%d/%m/%Y",        # 15/01/2025
        "%Y/%m/%d",        # 2025/01/15
    ]:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    # Extract just a year if nothing else works
    year_match = re.search(r"\b(20\d{2})\b", date_str)
    if year_match:
        year = int(year_match.group(1))
        return datetime(year, 6, 15, tzinfo=timezone.utc)  # Mid-year estimate

    return None
