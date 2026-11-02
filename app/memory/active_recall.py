"""
Nexus - Active Memory Recall (OpenClaw-inspired).

Pre-reply memory search sub-agent that automatically pulls in
relevant preferences, context, and past details before the main
agent reply. Users don't need to say "remember this" or "search memory".

Inspired by:
- OpenClaw extensions/active-memory/ — dedicated memory sub-agent
- OpenClaw docs: https://docs.openclaw.ai/concepts/active-memory

Features:
- Pre-reply automatic recall from vector + FTS memory
- Configurable context modes: message / recent / full
- Relevance scoring and deduplication
- Hidden prompt injection (not visible to user)
- Opt-in transcript persistence for debugging
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class RecallMode(str, Enum):
    """How much context to search."""
    MESSAGE = "message"    # Only current message
    RECENT = "recent"      # Recent N messages
    FULL = "full"          # Full session context


class RecallSource(str, Enum):
    """Where memories come from."""
    VECTOR = "vector"       # pgvector similarity search
    FTS = "fts"             # Full-text search
    GRAPH = "graph"         # Knowledge graph entities
    MEMORY_FILE = "file"    # MEMORY.md / USER.md


@dataclass
class RecalledMemory:
    """A single recalled memory fragment."""
    content: str
    source: RecallSource
    relevance_score: float = 0.0
    memory_type: str = ""      # episodic, semantic, preference, etc.
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecallResult:
    """Result of active memory recall."""
    memories: list[RecalledMemory] = field(default_factory=list)
    query_used: str = ""
    mode: RecallMode = RecallMode.MESSAGE
    search_time_ms: float = 0.0
    total_candidates: int = 0
    prompt_injection: str = ""  # Formatted text to inject into prompt

    @property
    def has_memories(self) -> bool:
        return len(self.memories) > 0


class ActiveMemoryRecall:
    """
    Pre-reply memory search that runs before the main agent turn.

    Flow:
        1. Extract search query from current message + recent context
        2. Search memory (vector + FTS + graph)
        3. Rank and deduplicate results
        4. Format as hidden prompt prefix
        5. Agent receives recalled context automatically

    Usage:
        recall = ActiveMemoryRecall(memory_manager=mm, llm_client=llm)
        result = await recall.search(
            current_message="What's my preferred coding style?",
            session_messages=last_5_messages,
        )
        if result.has_memories:
            # Inject into system prompt as hidden context
            system_prompt += result.prompt_injection
    """

    def __init__(
        self,
        memory_manager: Any = None,  # MemoryManager instance
        llm_client: Any = None,      # LLMClient for query refinement
        mode: RecallMode = RecallMode.RECENT,
        max_results: int = 5,
        min_relevance: float = 0.3,
        max_context_chars: int = 2000,
        enable_query_refinement: bool = False,
    ) -> None:
        self._memory = memory_manager
        self._llm = llm_client
        self.mode = mode
        self.max_results = max_results
        self.min_relevance = min_relevance
        self.max_context_chars = max_context_chars
        self.enable_query_refinement = enable_query_refinement
        self._stats = {"searches": 0, "memories_found": 0, "injections": 0}

    async def search(
        self,
        current_message: str,
        session_messages: list[dict[str, str]] | None = None,
        agent_id: str | None = None,
    ) -> RecallResult:
        """Search memory for relevant context before agent reply.

        Args:
            current_message: The user's current message.
            session_messages: Recent session messages for context.
            agent_id: Optional agent ID for per-agent memory.

        Returns:
            RecallResult with memories and formatted prompt injection.
        """
        t0 = time.time()
        self._stats["searches"] += 1

        # Build search query based on mode
        query = await self._build_query(current_message, session_messages)
        if not query:
            return RecallResult(query_used="", mode=self.mode)

        # Search across sources
        candidates: list[RecalledMemory] = []

        # Vector search (pgvector similarity)
        vector_results = await self._search_vector(query, agent_id)
        candidates.extend(vector_results)

        # Full-text search
        fts_results = await self._search_fts(query, agent_id)
        candidates.extend(fts_results)

        # Memory files (MEMORY.md, USER.md)
        file_results = await self._search_memory_files(query, agent_id)
        candidates.extend(file_results)

        # Knowledge graph entities
        graph_results = await self._search_graph(query)
        candidates.extend(graph_results)

        # Rank, deduplicate, and filter
        memories = self._rank_and_filter(candidates)
        total_candidates = len(candidates)

        dt = (time.time() - t0) * 1000

        if memories:
            self._stats["memories_found"] += len(memories)
            self._stats["injections"] += 1

        # Format prompt injection
        injection = self._format_injection(memories, query)

        result = RecallResult(
            memories=memories,
            query_used=query,
            mode=self.mode,
            search_time_ms=dt,
            total_candidates=total_candidates,
            prompt_injection=injection,
        )

        if memories:
            logger.info("active_recall_found",
                        query=query[:100],
                        found=len(memories),
                        candidates=total_candidates,
                        time_ms=round(dt, 1))
        else:
            logger.debug("active_recall_empty", query=query[:100], time_ms=round(dt, 1))

        return result

    async def _build_query(
        self,
        current_message: str,
        session_messages: list[dict[str, str]] | None,
    ) -> str:
        """Build search query from current message and context."""
        if not current_message.strip():
            return ""

        if self.mode == RecallMode.MESSAGE:
            return current_message.strip()[:500]

        elif self.mode == RecallMode.RECENT:
            parts = []
            if session_messages:
                # Use last 3 messages for context
                for msg in session_messages[-3:]:
                    content = msg.get("content", "")
                    if content:
                        parts.append(content[:200])
            parts.append(current_message)
            return " ".join(parts)[:500]

        elif self.mode == RecallMode.FULL:
            # Use LLM to extract key topics if available
            if self.enable_query_refinement and self._llm:
                return await self._refine_query(current_message, session_messages)
            return current_message.strip()[:500]

        return current_message.strip()[:500]

    async def _refine_query(
        self,
        message: str,
        session_messages: list[dict[str, str]] | None,
    ) -> str:
        """Use LLM to extract key search terms from context."""
        if not self._llm:
            return message[:500]

        try:
            context = ""
            if session_messages:
                context = "\n".join(
                    f"{m.get('role', 'user')}: {m.get('content', '')[:100]}"
                    for m in session_messages[-5:]
                )

            resp = await self._llm.chat(
                messages=[{
                    "role": "system",
                    "content": "Extract 3-5 key search terms from this conversation for memory recall. Return ONLY the terms, comma-separated."
                }, {
                    "role": "user",
                    "content": f"Context:\n{context}\n\nCurrent: {message[:300]}"
                }],
                max_tokens=50,
                temperature=0,
            )
            terms = resp.get("content", message)[:200]
            return terms if terms.strip() else message[:500]
        except Exception:
            return message[:500]

    async def _search_vector(
        self, query: str, agent_id: str | None,
    ) -> list[RecalledMemory]:
        """Search pgvector for similar memories."""
        if not self._memory:
            return []
        try:
            import uuid
            aid = uuid.UUID(agent_id) if agent_id else None
            results = await self._memory.search_memories(
                query=query,
                agent_id=aid,
                limit=self.max_results,
            )
            return [
                RecalledMemory(
                    content=r.get("content", ""),
                    source=RecallSource.VECTOR,
                    relevance_score=r.get("similarity", 0.0),
                    memory_type=r.get("memory_type", ""),
                    created_at=r.get("created_at", 0),
                )
                for r in results
                if r.get("content")
            ]
        except Exception as exc:
            logger.debug("active_recall_vector_error", error=str(exc))
            return []

    async def _search_fts(
        self, query: str, agent_id: str | None,
    ) -> list[RecalledMemory]:
        """Search full-text index for keyword matches."""
        # FTS search via session_search or memory FTS
        try:
            from app.core.session_search import search_sessions
            results = await search_sessions(query, limit=self.max_results)
            return [
                RecalledMemory(
                    content=r.get("content", "")[:500],
                    source=RecallSource.FTS,
                    relevance_score=0.5,
                    memory_type="session",
                )
                for r in results
                if r.get("content")
            ]
        except Exception:
            return []

    async def _search_memory_files(
        self, query: str, agent_id: str | None,
    ) -> list[RecalledMemory]:
        """Search MEMORY.md and USER.md files."""
        memories: list[RecalledMemory] = []
        try:
            from app.memory.agent_memory_files import AgentMemoryFiles
            amf = AgentMemoryFiles()

            # Check MEMORY.md
            memory_content = await amf.read_memory()
            if memory_content and query.lower() in memory_content.lower():
                # Simple keyword match — extract relevant section
                lines = memory_content.split("\n")
                query_lower = query.lower()
                relevant = [l for l in lines if any(
                    w in l.lower() for w in query_lower.split()[:3]
                )]
                if relevant:
                    memories.append(RecalledMemory(
                        content="\n".join(relevant[:10]),
                        source=RecallSource.MEMORY_FILE,
                        relevance_score=0.7,
                        memory_type="persistent_memory",
                    ))

            # Check USER.md
            user_content = await amf.read_user_profile()
            if user_content:
                memories.append(RecalledMemory(
                    content=user_content[:500],
                    source=RecallSource.MEMORY_FILE,
                    relevance_score=0.6,
                    memory_type="user_profile",
                ))
        except Exception:
            pass

        return memories

    async def _search_graph(self, query: str) -> list[RecalledMemory]:
        """Search knowledge graph for entity matches."""
        if not self._memory:
            return []
        try:
            nodes = await self._memory.search_knowledge(query, limit=3)
            return [
                RecalledMemory(
                    content=f"{n.get('label', '')}: {n.get('properties', {}).get('description', '')}",
                    source=RecallSource.GRAPH,
                    relevance_score=0.5,
                    memory_type="knowledge_entity",
                )
                for n in nodes
                if n.get("label")
            ]
        except Exception:
            return []

    def _rank_and_filter(
        self, candidates: list[RecalledMemory],
    ) -> list[RecalledMemory]:
        """Rank by relevance, deduplicate, and filter."""
        if not candidates:
            return []

        # Deduplicate by content similarity (simple hash)
        seen_hashes: set[int] = set()
        unique: list[RecalledMemory] = []
        for m in candidates:
            h = hash(m.content[:100])
            if h not in seen_hashes:
                seen_hashes.add(h)
                unique.append(m)

        # Filter by minimum relevance
        filtered = [m for m in unique if m.relevance_score >= self.min_relevance]

        # Sort by relevance (descending)
        filtered.sort(key=lambda m: m.relevance_score, reverse=True)

        # Limit results
        return filtered[:self.max_results]

    def _format_injection(
        self, memories: list[RecalledMemory], query: str,
    ) -> str:
        """Format memories as hidden prompt prefix."""
        if not memories:
            return ""

        parts = ["<recalled_memory>"]
        total_chars = 0
        for m in memories:
            entry = f"[{m.source.value}/{m.memory_type}] {m.content}"
            if total_chars + len(entry) > self.max_context_chars:
                break
            parts.append(entry)
            total_chars += len(entry)
        parts.append("</recalled_memory>")

        return "\n".join(parts)

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)
