"""
Nexus - Active Memory Files (OpenClaw-inspired).

Per-agent persistent memory files that auto-inject into context.

Inspired by:
- OpenClaw extensions/active-memory/ — MEMORY.md management
- OpenClaw SECURITY.md — Workspace Memory Trust Boundary
- OpenClaw CHANGELOG.md — Active Memory features

Features:
- Per-agent MEMORY.md management (read/write/search)
- Surgical editing: append, replace, remove specific entries
- Auto-inject relevant memories into system prompt
- Category-based organization (preferences, facts, tasks, instructions)
- Token-budget-aware injection (won't exceed allocated budget)
- Integration with Dreaming Engine for memory promotion
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Base directory for memory files
MEMORY_BASE_DIR = Path("workspace_data/memory")
GLOBAL_MEMORY_FILE = Path("workspace_data/MEMORY.md")


class MemoryCategory(str, Enum):
    """Categories for organizing memory entries."""
    PREFERENCE = "preference"       # User preferences and style
    FACT = "fact"                    # Known facts about user/project
    INSTRUCTION = "instruction"     # Standing instructions
    TASK = "task"                    # Task outcomes and learnings
    CONTEXT = "context"             # Contextual information
    CORRECTION = "correction"       # Corrections to previous behavior


@dataclass
class MemoryEntry:
    """A single memory entry."""
    content: str
    category: MemoryCategory = MemoryCategory.FACT
    added_at: float = field(default_factory=time.time)
    source: str = ""                # Where this memory came from
    confidence: float = 1.0         # How confident (0-1)
    hash: str = ""

    def __post_init__(self):
        if not self.hash:
            self.hash = hashlib.sha256(
                self.content.encode("utf-8")
            ).hexdigest()[:12]

    def format_markdown(self) -> str:
        """Format as markdown line for MEMORY.md."""
        return f"- [{self.category.value}] {self.content}"


@dataclass
class MemoryInjection:
    """Result of memory injection into context."""
    content: str                    # Formatted memory text for injection
    entries_included: int = 0
    total_entries: int = 0
    estimated_tokens: int = 0
    truncated: bool = False


class ActiveMemoryManager:
    """
    Per-agent persistent memory file management.

    Each agent has a MEMORY.md file containing durable memories
    organized by category. Memories are auto-injected into the
    system prompt at the beginning of each conversation turn.

    Usage:
        mgr = ActiveMemoryManager(agent_id="main-agent")

        # Add memory
        await mgr.append("User prefers Vietnamese responses", MemoryCategory.PREFERENCE)

        # Get injection for context
        injection = await mgr.get_context_injection(token_budget=2000)
        system_prompt = f"{base_prompt}\\n\\n{injection.content}"

        # Search
        results = await mgr.search("coding style")

        # Edit
        await mgr.replace("old preference", "new preference")
        await mgr.remove("outdated fact")
    """

    def __init__(
        self,
        agent_id: str = "default",
        base_dir: Optional[Path] = None,
        tokens_per_char: float = 0.3,  # Approximate token/char ratio
    ) -> None:
        self._agent_id = agent_id
        self._base_dir = base_dir or MEMORY_BASE_DIR
        self._tokens_per_char = tokens_per_char
        self._cache: Optional[str] = None
        self._cache_time: float = 0.0
        self._cache_ttl: float = 5.0  # Cache for 5 seconds

    @property
    def memory_file(self) -> Path:
        """Path to this agent's memory file."""
        return self._base_dir / f"{self._agent_id}.md"

    @property
    def agent_id(self) -> str:
        return self._agent_id

    # ── Read Operations ──────────────────────────────────────────

    async def get_all(self) -> str:
        """Get full memory file content."""
        if not self.memory_file.exists():
            return ""
        return self.memory_file.read_text(encoding="utf-8")

    async def get_entries(self) -> list[MemoryEntry]:
        """Parse memory file into structured entries."""
        content = await self.get_all()
        if not content:
            return []

        entries: list[MemoryEntry] = []
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entry = self._parse_entry(line)
            if entry:
                entries.append(entry)
        return entries

    async def get_by_category(self, category: MemoryCategory) -> list[MemoryEntry]:
        """Get entries filtered by category."""
        entries = await self.get_entries()
        return [e for e in entries if e.category == category]

    async def search(self, query: str, limit: int = 10) -> list[MemoryEntry]:
        """Search memories by keyword relevance.

        Args:
            query: Search query text.
            limit: Max results to return.

        Returns:
            List of matching entries sorted by relevance.
        """
        entries = await self.get_entries()
        if not entries:
            return []

        # Keyword-based scoring
        keywords = [kw.lower() for kw in query.split() if len(kw) >= 2]
        if not keywords:
            return entries[:limit]

        scored: list[tuple[MemoryEntry, float]] = []
        for entry in entries:
            content_lower = entry.content.lower()
            score = sum(1.0 for kw in keywords if kw in content_lower)
            if score > 0:
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [entry for entry, _ in scored[:limit]]

    # ── Write Operations ─────────────────────────────────────────

    async def append(
        self,
        content: str,
        category: MemoryCategory = MemoryCategory.FACT,
        source: str = "",
        deduplicate: bool = True,
    ) -> bool:
        """Append a new memory entry.

        Args:
            content: Memory content to add.
            category: Category for organization.
            source: Where this memory came from.
            deduplicate: Skip if similar entry exists.

        Returns:
            True if added, False if duplicate.
        """
        content = content.strip()
        if not content:
            return False

        # Check for duplicates
        if deduplicate:
            existing = await self.get_all()
            if content[:100] in existing:
                logger.debug("memory_duplicate_skipped", content=content[:50])
                return False

        entry = MemoryEntry(
            content=content,
            category=category,
            source=source,
        )

        # Ensure directory exists
        self._base_dir.mkdir(parents=True, exist_ok=True)

        # Read existing or create new
        if self.memory_file.exists():
            existing = self.memory_file.read_text(encoding="utf-8")
        else:
            existing = self._create_header()

        # Append entry
        new_content = existing.rstrip() + "\n" + entry.format_markdown() + "\n"
        self.memory_file.write_text(new_content, encoding="utf-8")
        self._invalidate_cache()

        logger.debug("memory_appended",
                     agent=self._agent_id, category=category.value,
                     content_preview=content[:50])
        return True

    async def replace(self, old_text: str, new_text: str) -> bool:
        """Replace a memory entry by matching text.

        Args:
            old_text: Text to find (substring match).
            new_text: Text to replace with.

        Returns:
            True if replacement was made.
        """
        if not self.memory_file.exists():
            return False

        content = self.memory_file.read_text(encoding="utf-8")
        if old_text not in content:
            return False

        updated = content.replace(old_text, new_text, 1)
        self.memory_file.write_text(updated, encoding="utf-8")
        self._invalidate_cache()

        logger.info("memory_replaced",
                    agent=self._agent_id,
                    old=old_text[:30], new=new_text[:30])
        return True

    async def remove(self, pattern: str) -> int:
        """Remove memory entries matching a pattern.

        Args:
            pattern: Text pattern to match (substring).

        Returns:
            Number of entries removed.
        """
        if not self.memory_file.exists():
            return 0

        content = self.memory_file.read_text(encoding="utf-8")
        lines = content.split("\n")
        original_count = len(lines)

        # Keep lines that DON'T match the pattern
        filtered = [
            line for line in lines
            if pattern.lower() not in line.lower() or line.startswith("#")
        ]

        removed = original_count - len(filtered)
        if removed > 0:
            self.memory_file.write_text("\n".join(filtered), encoding="utf-8")
            self._invalidate_cache()
            logger.info("memory_removed",
                        agent=self._agent_id, pattern=pattern[:30], count=removed)

        return removed

    async def clear(self) -> None:
        """Clear all memory entries (keeps header)."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self.memory_file.write_text(self._create_header(), encoding="utf-8")
        self._invalidate_cache()
        logger.info("memory_cleared", agent=self._agent_id)

    # ── Context Injection ────────────────────────────────────────

    async def get_context_injection(
        self,
        token_budget: int = 2000,
        query: Optional[str] = None,
        categories: Optional[list[MemoryCategory]] = None,
    ) -> MemoryInjection:
        """Get formatted memory for injection into system prompt.

        Selects and formats memories within a token budget.
        If query is provided, prioritizes relevant memories.

        Args:
            token_budget: Maximum tokens to use.
            query: Optional relevance query for prioritization.
            categories: Optional filter by categories.

        Returns:
            MemoryInjection with formatted content.
        """
        entries = await self.get_entries()
        total_entries = len(entries)

        if not entries:
            return MemoryInjection(
                content="", entries_included=0,
                total_entries=0, estimated_tokens=0,
            )

        # Filter by categories if specified
        if categories:
            entries = [e for e in entries if e.category in categories]

        # Prioritize by relevance if query provided
        if query:
            entries = self._prioritize_by_relevance(entries, query)

        # Build injection text within budget
        char_budget = int(token_budget / self._tokens_per_char)
        header = "<active_memory>\n"
        footer = "\n</active_memory>"
        overhead = len(header) + len(footer)
        remaining = char_budget - overhead

        lines: list[str] = []
        included = 0

        for entry in entries:
            line = entry.format_markdown()
            if len(line) + 1 > remaining:
                break
            lines.append(line)
            remaining -= len(line) + 1  # +1 for newline
            included += 1

        if not lines:
            return MemoryInjection(
                content="", entries_included=0,
                total_entries=total_entries, estimated_tokens=0,
            )

        content = header + "\n".join(lines) + footer
        estimated_tokens = int(len(content) * self._tokens_per_char)

        return MemoryInjection(
            content=content,
            entries_included=included,
            total_entries=total_entries,
            estimated_tokens=estimated_tokens,
            truncated=included < len(entries),
        )

    # ── Utility ──────────────────────────────────────────────────

    def _prioritize_by_relevance(
        self,
        entries: list[MemoryEntry],
        query: str,
    ) -> list[MemoryEntry]:
        """Sort entries by relevance to query."""
        keywords = [kw.lower() for kw in query.split() if len(kw) >= 2]
        if not keywords:
            return entries

        def score(entry: MemoryEntry) -> float:
            content_lower = entry.content.lower()
            base = sum(1.0 for kw in keywords if kw in content_lower)
            # Boost preferences and instructions
            if entry.category in (MemoryCategory.PREFERENCE, MemoryCategory.INSTRUCTION):
                base += 0.5
            return base

        return sorted(entries, key=score, reverse=True)

    def _parse_entry(self, line: str) -> Optional[MemoryEntry]:
        """Parse a markdown line into a MemoryEntry."""
        line = line.strip()
        if not line.startswith("- "):
            return None

        content = line[2:]  # Remove "- " prefix

        # Try to extract category tag: [category]
        category = MemoryCategory.FACT
        category_match = re.match(r"\[(\w+)\]\s*(.*)", content)
        if category_match:
            try:
                category = MemoryCategory(category_match.group(1).lower())
                content = category_match.group(2)
            except ValueError:
                pass  # Unknown category, keep as fact

        if not content.strip():
            return None

        return MemoryEntry(content=content.strip(), category=category)

    def _create_header(self) -> str:
        """Create initial memory file header."""
        return (
            f"# Agent Memory: {self._agent_id}\n\n"
            f"_Managed by Nexus Active Memory System_\n\n"
        )

    def _invalidate_cache(self) -> None:
        """Invalidate the content cache."""
        self._cache = None
        self._cache_time = 0.0

    async def get_stats(self) -> dict[str, Any]:
        """Return memory statistics."""
        entries = await self.get_entries()
        category_counts: dict[str, int] = {}
        for entry in entries:
            cat = entry.category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1

        file_size = 0
        if self.memory_file.exists():
            file_size = self.memory_file.stat().st_size

        return {
            "agent_id": self._agent_id,
            "total_entries": len(entries),
            "category_counts": category_counts,
            "file_size_bytes": file_size,
            "file_exists": self.memory_file.exists(),
        }


# ─────────────────────────────────────────────────────────────────
# Factory & Manager Registry
# ─────────────────────────────────────────────────────────────────

_managers: dict[str, ActiveMemoryManager] = {}


def get_active_memory(agent_id: str = "default") -> ActiveMemoryManager:
    """Get or create an ActiveMemoryManager for an agent."""
    if agent_id not in _managers:
        _managers[agent_id] = ActiveMemoryManager(agent_id=agent_id)
    return _managers[agent_id]


def list_memory_agents() -> list[str]:
    """List all agents with active memory files."""
    if not MEMORY_BASE_DIR.exists():
        return []
    return [
        f.stem for f in MEMORY_BASE_DIR.glob("*.md")
        if f.is_file()
    ]
