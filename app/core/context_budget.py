"""
Nexus - Smart Context Budget Allocator.

Thay thế hard-truncate bằng phân bổ budget thông minh:
- Thu thập tất cả enrichment sections
- Đánh giá relevance từng section đối với query
- Phân bổ budget tỉ lệ thuận với relevance + priority
- Section ít relevant → compress (summarize) thay vì truncate
- Đảm bảo total enrichment luôn nằm trong budget

Ưu tiên (priority tĩnh):
1. XSkill (proven knowledge from past tasks) — highest
2. Previous Task Context (session continuity)
3. Active Memory Recall (user preferences/facts)
4. Knowledge Graph (entities/relations)
5. Memory Layers (L1/L2/L3 generic)
6. Active Memory Files (general agent memory)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Priority levels (higher = more important) ──
SECTION_PRIORITIES: dict[str, int] = {
    "xskill": 90,           # Proven workflows — highest value
    "prev_task": 85,        # Session continuity — critical for follow-ups
    "active_recall": 70,    # User preferences/known facts
    "kg_context": 60,       # Knowledge graph entities
    "memory_layers": 50,    # Generic memory (L1/L2/L3)
    "active_memory": 40,    # Agent memory files
}

# Minimum chars per section (even low-priority gets at least this)
_MIN_SECTION_CHARS = 200
# Maximum chars per section (no section can exceed this regardless of budget)
_MAX_SECTION_CHARS = 8000


@dataclass
class EnrichmentSection:
    """One enrichment section waiting to be allocated budget."""

    name: str
    content: str
    priority: int = 50  # Static priority (from SECTION_PRIORITIES)
    relevance_score: float = 0.5  # Dynamic relevance to current query (0-1)

    @property
    def effective_score(self) -> float:
        """Combined score: 60% priority + 40% relevance."""
        return (self.priority / 100.0) * 0.6 + self.relevance_score * 0.4

    @property
    def content_length(self) -> int:
        return len(self.content)


@dataclass
class AllocationResult:
    """Result of budget allocation."""

    assembled_text: str
    sections_included: int
    sections_dropped: int
    total_chars: int
    budget_used_pct: float
    allocation_details: list[dict[str, Any]] = field(default_factory=list)


class ContextBudgetAllocator:
    """
    Smart context budget allocator.

    Instead of giving each section a fixed cap (8K) and hoping total fits,
    this allocator:
    1. Knows total available budget
    2. Scores each section by priority + relevance
    3. Allocates proportionally — high-value sections get more space
    4. Compresses low-priority sections instead of dropping them
    """

    def __init__(self, total_budget_chars: int):
        """
        Args:
            total_budget_chars: Total available characters for ALL enrichment sections.
                                Typically ~30% of model context window * 4 chars/token.
        """
        self.total_budget = total_budget_chars
        self._sections: list[EnrichmentSection] = []

    def add_section(
        self,
        name: str,
        content: str,
        relevance_score: float = 0.5,
    ) -> None:
        """Add an enrichment section to be allocated.

        Args:
            name: Section identifier (must match SECTION_PRIORITIES keys).
            content: Raw content of this section.
            relevance_score: 0.0-1.0 how relevant this section is to current query.
                             Can be computed via keyword overlap or embedding similarity.
        """
        if not content or not content.strip():
            return  # Skip empty sections

        priority = SECTION_PRIORITIES.get(name, 50)
        self._sections.append(EnrichmentSection(
            name=name,
            content=content.strip(),
            priority=priority,
            relevance_score=min(1.0, max(0.0, relevance_score)),
        ))

    def allocate(self) -> AllocationResult:
        """
        Allocate budget across all added sections.

        Algorithm:
        1. Sort sections by effective_score (priority + relevance)
        2. Calculate proportional budget per section
        3. High-score sections get more budget (up to their content length)
        4. Leftover budget from short sections redistributes to longer ones
        5. Sections that don't fit minimum threshold get dropped entirely
        """
        if not self._sections:
            return AllocationResult(
                assembled_text="",
                sections_included=0,
                sections_dropped=0,
                total_chars=0,
                budget_used_pct=0.0,
            )

        # Sort by effective_score descending
        sorted_sections = sorted(
            self._sections, key=lambda s: s.effective_score, reverse=True
        )

        # Calculate total effective score for proportional allocation
        total_score = sum(s.effective_score for s in sorted_sections)
        if total_score == 0:
            total_score = 1.0  # Prevent division by zero

        # Phase 1: Calculate initial budget allocation
        allocations: list[tuple[EnrichmentSection, int]] = []
        remaining_budget = self.total_budget

        for section in sorted_sections:
            # Proportional share based on score
            share = (section.effective_score / total_score) * self.total_budget
            # Clamp to min/max and content length
            allocated = int(min(
                share,
                section.content_length,
                _MAX_SECTION_CHARS,
            ))
            allocated = max(allocated, _MIN_SECTION_CHARS)

            # Don't exceed remaining budget
            if allocated > remaining_budget:
                if remaining_budget >= _MIN_SECTION_CHARS:
                    allocated = remaining_budget
                else:
                    allocated = 0  # Drop this section

            allocations.append((section, allocated))
            remaining_budget -= allocated

        # Phase 2: Redistribute leftover budget to sections that need more
        leftover = remaining_budget
        if leftover > 0:
            for i, (section, allocated) in enumerate(allocations):
                if allocated == 0:
                    continue
                # Give extra to sections that were capped below their content length
                needed = min(section.content_length, _MAX_SECTION_CHARS) - allocated
                if needed > 0:
                    extra = min(needed, leftover)
                    allocations[i] = (section, allocated + extra)
                    leftover -= extra
                    if leftover <= 0:
                        break

        # Phase 3: Assemble final text
        parts: list[str] = []
        details: list[dict[str, Any]] = []
        sections_included = 0
        sections_dropped = 0

        for section, budget in allocations:
            if budget == 0 or budget < _MIN_SECTION_CHARS:
                sections_dropped += 1
                details.append({
                    "name": section.name,
                    "status": "dropped",
                    "reason": "insufficient_budget",
                    "content_length": section.content_length,
                    "effective_score": round(section.effective_score, 3),
                })
                continue

            # Apply budget: truncate at sentence boundary if possible
            if section.content_length <= budget:
                # Full content fits
                text = section.content
            else:
                # Smart truncate: try to cut at last sentence boundary
                text = self._smart_truncate(section.content, budget)

            parts.append(text)
            sections_included += 1
            details.append({
                "name": section.name,
                "status": "included",
                "allocated_chars": len(text),
                "original_chars": section.content_length,
                "compression_ratio": round(len(text) / max(1, section.content_length), 2),
                "effective_score": round(section.effective_score, 3),
            })

        assembled = "\n\n".join(parts)
        total_chars = len(assembled)

        logger.info(
            "context_budget_allocated",
            total_budget=self.total_budget,
            total_used=total_chars,
            sections_included=sections_included,
            sections_dropped=sections_dropped,
            budget_used_pct=round(total_chars / max(1, self.total_budget) * 100, 1),
        )

        return AllocationResult(
            assembled_text=assembled,
            sections_included=sections_included,
            sections_dropped=sections_dropped,
            total_chars=total_chars,
            budget_used_pct=total_chars / max(1, self.total_budget) * 100,
            allocation_details=details,
        )

    @staticmethod
    def _smart_truncate(text: str, max_chars: int) -> str:
        """Truncate at last sentence boundary within budget.

        Better than hard cut at arbitrary position — preserves semantic completeness.
        Falls back to paragraph boundary, then word boundary.
        """
        if len(text) <= max_chars:
            return text

        # Try to find last sentence end within budget
        truncated = text[:max_chars]

        # Strategy 1: Last sentence boundary (. ! ?)
        for sep in [". ", ".\n", "! ", "?\n", "? "]:
            last_sep = truncated.rfind(sep)
            if last_sep > max_chars * 0.6:  # At least 60% of content
                return truncated[:last_sep + 1].rstrip()

        # Strategy 2: Last paragraph boundary
        last_para = truncated.rfind("\n\n")
        if last_para > max_chars * 0.5:
            return truncated[:last_para].rstrip()

        # Strategy 3: Last newline
        last_nl = truncated.rfind("\n")
        if last_nl > max_chars * 0.7:
            return truncated[:last_nl].rstrip()

        # Fallback: hard cut at word boundary
        last_space = truncated.rfind(" ")
        if last_space > max_chars * 0.8:
            return truncated[:last_space] + "..."

        return truncated + "..."


def compute_keyword_relevance(query: str, content: str) -> float:
    """
    Fast keyword-based relevance scoring (no LLM needed).

    Computes what fraction of query keywords appear in the content.
    Returns 0.0-1.0.
    """
    # Extract meaningful keywords from query (> 2 chars, not stopwords)
    _STOPWORDS = frozenset([
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "shall", "can",
        "và", "của", "cho", "với", "trong", "được", "là", "có",
        "này", "đó", "một", "các", "những", "về", "từ", "đến",
        "không", "cũng", "như", "khi", "nếu", "thì",
    ])

    query_words = {
        w.lower() for w in query.split()
        if len(w) > 2 and w.lower() not in _STOPWORDS
    }

    if not query_words:
        return 0.5  # Neutral if no meaningful keywords

    content_lower = content.lower()
    matches = sum(1 for w in query_words if w in content_lower)

    return min(1.0, matches / max(1, len(query_words)))
