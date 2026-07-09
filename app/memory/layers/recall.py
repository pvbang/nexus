"""
Memory Layers Recall — Hybrid search across L1/L2/L3.

Called before each agent turn to inject relevant memory context.
Search strategy:
- L3 Persona: Always inject (if exists) — highest priority
- L2 Scenes: Keyword match scene titles/content against user query
- L1 Atoms: Hybrid search (FTS + importance ranking) ranked by relevance
"""
from __future__ import annotations

import asyncio
import time
import structlog
from typing import Optional

from sqlalchemy import select, text, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.layers.models import (
    MemoryAtomModel,
    MemorySceneModel,
    MemoryPersonaModel,
    RecallResult,
    RecallResponse,
)
from app.memory.layers.config import MemoryLayersConfig

logger = structlog.get_logger(__name__)


class MemoryLayerRecall:
    """Hybrid recall across memory layers L1-L3."""

    def __init__(self, config: MemoryLayersConfig):
        self.config = config

    async def recall(
        self,
        db: AsyncSession,
        query: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        session_id: str | None = None,
    ) -> RecallResponse:
        """
        Perform hybrid recall across all memory layers.

        Returns context to inject into system prompt before agent turn.
        Respects token budgets per layer and overall timeout.
        """
        start_ms = time.monotonic()
        timeout_s = self.config.recall_timeout_ms / 1000.0

        try:
            results = await asyncio.wait_for(
                self._parallel_search(db, query, user_id, agent_id),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - start_ms) * 1000)
            logger.warning(
                "recall_timeout",
                elapsed_ms=elapsed,
                timeout_ms=self.config.recall_timeout_ms,
            )
            return RecallResponse(duration_ms=elapsed, strategy="timeout")
        except Exception as e:
            elapsed = int((time.monotonic() - start_ms) * 1000)
            logger.error("recall_failed", error=str(e), elapsed_ms=elapsed)
            return RecallResponse(duration_ms=elapsed, strategy="error")

        elapsed = int((time.monotonic() - start_ms) * 1000)

        persona_snippet, l2_results, l1_results = results

        # Combine results
        all_results = l2_results + l1_results

        # Sort by score descending, take top N
        all_results.sort(key=lambda r: r.score, reverse=True)
        all_results = all_results[: self.config.recall_max_results]

        return RecallResponse(
            results=all_results,
            persona_snippet=persona_snippet,
            strategy=self.config.recall_strategy,
            duration_ms=elapsed,
        )

    async def _parallel_search(
        self,
        db: AsyncSession,
        query: str,
        user_id: str | None,
        agent_id: str | None,
    ) -> tuple[str | None, list[RecallResult], list[RecallResult]]:
        """Run L3, L2, L1 searches in parallel.

        HYBRID ISOLATION STRATEGY (Option C):
        - L3 Persona: per user (shared across agents) — "user preferences"
        - L2 Scenes: per user + agent (include NULL for backward compat)
        - L1 Atoms: per user + agent (strict per-agent isolation)
        """
        # L3: user_id only (persona is shared across all agents for same user)
        persona_task = self._get_persona(db, user_id)
        # L2: user_id + agent_id (with NULL fallback)
        l2_task = self._search_scenes(db, query, user_id, agent_id)
        # L1: user_id + agent_id (strict)
        l1_task = self._search_atoms(db, query, user_id, agent_id)

        persona, l2_results, l1_results = await asyncio.gather(
            persona_task, l2_task, l1_task, return_exceptions=True
        )

        # Handle exceptions gracefully
        if isinstance(persona, Exception):
            logger.debug("recall_l3_error", error=str(persona))
            persona = None
        if isinstance(l2_results, Exception):
            logger.debug("recall_l2_error", error=str(l2_results))
            l2_results = []
        if isinstance(l1_results, Exception):
            logger.debug("recall_l1_error", error=str(l1_results))
            l1_results = []

        return persona, l2_results, l1_results

    async def _get_persona(
        self, db: AsyncSession, user_id: str | None
    ) -> str | None:
        """Get latest persona content (L3).

        ISOLATION: Per user only — persona is SHARED across all agents.
        This ensures all agents know user preferences regardless of which
        agent originally captured them.
        """
        stmt = select(MemoryPersonaModel.content_md)
        if user_id:
            stmt = stmt.where(MemoryPersonaModel.user_id == user_id)
        # NOTE: No agent_id filter — persona shared across agents for same user
        stmt = stmt.order_by(MemoryPersonaModel.version.desc()).limit(1)

        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

        if row:
            # Truncate to token budget (~4 chars per token)
            max_chars = self.config.token_budget.l3_persona * 4
            return row[:max_chars] if len(row) > max_chars else row
        return None

    async def _search_scenes(
        self, db: AsyncSession, query: str, user_id: str | None, agent_id: str | None
    ) -> list[RecallResult]:
        """Search L2 scenes by multi-keyword relevance scoring.

        Uses OR-matching across extracted keywords, scoring scenes by how many
        keywords match. Much more effective than single ILIKE pattern which either
        matches the entire query (too strict) or first 100 chars (too broad).

        ISOLATION: Per user + agent, includes scenes with agent_id=NULL for backward compat.
        """
        if not query or len(query.strip()) < 3:
            return []

        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        # Build OR conditions: scene matches if ANY keyword hits title or content
        keyword_conditions = []
        for kw in keywords[:8]:  # Max 8 keywords to prevent query explosion
            pattern = f"%{kw}%"
            keyword_conditions.append(MemorySceneModel.title.ilike(pattern))
            keyword_conditions.append(MemorySceneModel.content_md.ilike(pattern))

        stmt = (
            select(MemorySceneModel)
            .where(
                MemorySceneModel.is_active == True,  # noqa: E712
                or_(*keyword_conditions),
            )
        )
        if user_id:
            stmt = stmt.where(MemorySceneModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(
                or_(
                    MemorySceneModel.agent_id == agent_id,
                    MemorySceneModel.agent_id.is_(None),
                )
            )
        stmt = stmt.order_by(MemorySceneModel.updated_at.desc()).limit(10)

        result = await db.execute(stmt)
        scenes = result.scalars().all()

        # Score by keyword overlap ratio
        scored: list[tuple[float, MemorySceneModel]] = []
        for scene in scenes:
            searchable = (scene.title + " " + scene.content_md).lower()
            hits = sum(1 for kw in keywords if kw in searchable)
            score = hits / len(keywords)  # 0.0-1.0 based on keyword coverage
            if score > 0:
                scored.append((score, scene))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_scenes = scored[:3]

        results = []
        max_chars = self.config.token_budget.l2_scenes * 4 // max(len(top_scenes), 1)

        for score, scene in top_scenes:
            content = f"**{scene.title}**\n{scene.content_md}"
            if len(content) > max_chars:
                content = content[:max_chars] + "..."

            results.append(
                RecallResult(
                    content=content,
                    layer="l2",
                    score=min(0.9, 0.4 + score * 0.5),  # Scale: 0.4-0.9 based on keyword coverage
                    source="multi_keyword",
                )
            )

        return results

    @staticmethod
    def _extract_keywords(query: str) -> list[str]:
        """Extract meaningful keywords from query, filtering stop words."""
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further", "then",
            "once", "that", "this", "these", "those", "what", "which", "who",
            "whom", "when", "where", "why", "how", "all", "each", "every", "both",
            "few", "more", "most", "other", "some", "such", "no", "nor", "not",
            "only", "own", "same", "so", "than", "too", "very", "just", "because",
            "and", "but", "or", "if", "while", "about", "it", "its", "my", "your",
            "his", "her", "their", "our", "i", "me", "you", "he", "she", "we", "they",
        }
        words = query.lower().split()
        keywords = [w.strip(".,!?;:'\"()[]{}") for w in words]
        keywords = [w for w in keywords if len(w) >= 3 and w not in stop_words]
        return keywords

    async def _search_atoms(
        self, db: AsyncSession, query: str, user_id: str | None, agent_id: str | None
    ) -> list[RecallResult]:
        """Hybrid search L1 atoms (FTS + importance ranking).

        ISOLATION: Strict per agent_id — each agent only sees its own atoms.
        """
        if not query or len(query.strip()) < 3:
            return []

        results: list[RecallResult] = []

        # Strategy 1: Full-text search via tsvector
        if self.config.recall_strategy in ("keyword", "hybrid"):
            fts_results = await self._fts_search_atoms(db, query, user_id, agent_id)
            results.extend(fts_results)

        # Strategy 2: Importance-based recent atoms (fallback/supplement)
        if len(results) < self.config.recall_max_results:
            importance_results = await self._importance_search_atoms(
                db,
                user_id,
                agent_id,
                exclude_contents=[r.content for r in results],
            )
            results.extend(importance_results)

        return results[: self.config.recall_max_results]

    async def _fts_search_atoms(
        self, db: AsyncSession, query: str, user_id: str | None, agent_id: str | None
    ) -> list[RecallResult]:
        """Full-text search on atoms using PostgreSQL tsvector.

        ISOLATION: Strict per agent_id. If agent_id is provided, only atoms
        belonging to that agent are returned. If NULL, all atoms are searched
        (backward compat / shared mode).
        """
        try:
            fts_query = query[:200]  # Limit query length

            # Build SQL dynamically to avoid asyncpg type ambiguity with
            # the pattern ($X IS NULL OR col = $X). When the parameter has
            # a concrete value, asyncpg cannot reconcile NULL-check typing.
            conditions = [
                "is_active = true",
                "search_vector @@ plainto_tsquery('english', :query)",
            ]
            params: dict = {
                "query": fts_query,
                "limit": self.config.recall_max_results,
            }
            if user_id is not None:
                conditions.append("user_id = :user_id")
                params["user_id"] = user_id
            if agent_id is not None:
                conditions.append("agent_id = :agent_id")
                params["agent_id"] = agent_id

            where_clause = " AND ".join(conditions)
            sql = text(f"""
                SELECT content, atom_type, importance,
                       ts_rank(search_vector, plainto_tsquery('english', :query)) as rank
                FROM memory_atoms
                WHERE {where_clause}
                ORDER BY rank DESC, importance DESC
                LIMIT :limit
            """)

            result = await db.execute(sql, params)

            rows = result.all()
            return [
                RecallResult(
                    content=row[0],
                    layer="l1",
                    score=min(1.0, float(row[3]) * 0.5 + float(row[2]) * 0.5),
                    atom_type=row[1],
                    source="fts",
                )
                for row in rows
            ]
        except Exception as e:
            logger.debug("fts_search_failed", error=str(e))
            # Rollback to clear aborted transaction state and prevent cascade
            # failure for subsequent queries on the same DB session.
            try:
                await db.rollback()
            except Exception:
                pass
            return []

    async def _importance_search_atoms(
        self,
        db: AsyncSession,
        user_id: str | None,
        agent_id: str | None,
        exclude_contents: list[str] | None = None,
    ) -> list[RecallResult]:
        """Get high-importance recent atoms as fallback.

        ISOLATION: Strict per agent_id — only atoms from current agent.
        """
        stmt = select(MemoryAtomModel).where(
            MemoryAtomModel.is_active == True,  # noqa: E712
            MemoryAtomModel.importance >= 0.7,
        )
        if user_id:
            stmt = stmt.where(MemoryAtomModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemoryAtomModel.agent_id == agent_id)
        stmt = stmt.order_by(
            MemoryAtomModel.importance.desc(), MemoryAtomModel.created_at.desc()
        ).limit(5)

        result = await db.execute(stmt)
        atoms = result.scalars().all()

        results = []
        for atom in atoms:
            if exclude_contents and atom.content in exclude_contents:
                continue
            results.append(
                RecallResult(
                    content=atom.content,
                    layer="l1",
                    score=float(atom.importance) * 0.8,
                    atom_type=atom.atom_type.value if atom.atom_type else None,
                    source="importance",
                )
            )

        return results

    def format_for_injection(self, recall_response: RecallResponse) -> str:
        """Format recall results as context string for system prompt injection."""
        if not recall_response.results and not recall_response.persona_snippet:
            return ""

        sections: list[str] = []

        # L3 Persona first (highest priority)
        if recall_response.persona_snippet:
            sections.append(
                f"<user-profile>\n{recall_response.persona_snippet}\n</user-profile>"
            )

        # L1/L2 memories
        if recall_response.results:
            memory_lines = []
            for r in recall_response.results:
                prefix = (
                    f"[{r.layer.upper()}:{r.atom_type}]"
                    if r.atom_type
                    else f"[{r.layer.upper()}]"
                )
                memory_lines.append(f"- {prefix} {r.content}")

            memories_block = "\n".join(memory_lines)
            sections.append(
                f"<relevant-memories>\n{memories_block}\n</relevant-memories>"
            )

        return "\n\n".join(sections)
