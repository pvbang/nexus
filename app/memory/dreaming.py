"""
MekongAI Nexus - Dreaming Memory Promotion (OpenClaw-inspired).

3-phase background memory consolidation system that promotes
short-term session memories into durable long-term storage.

Inspired by:
- OpenClaw extensions/memory-core/ — dreaming phases
- OpenClaw CHANGELOG.md — dreaming/memory-core features
- Human sleep memory consolidation (light/deep/REM analogy)

Phases:
1. Light Sleep — Scan recent conversations, stage candidates
2. Deep Sleep  — Score and promote important memories to MEMORY.md
3. REM Sleep   — Generate narrative diary, cross-reference knowledge

Flow:
    Scheduler triggers dreaming (e.g., daily at 3AM)
    → Light: extract candidate memories from recent sessions
    → Deep: score importance, promote to durable storage
    → REM: generate dream diary narrative (DREAMS.md)
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

DREAMS_FILE = Path("workspace_data/DREAMS.md")
MEMORY_FILE = Path("workspace_data/MEMORY.md")


class DreamPhase(str, Enum):
    """Dreaming phases inspired by human sleep cycles."""
    IDLE = "idle"
    LIGHT = "light"    # Scan & stage
    DEEP = "deep"      # Score & promote
    REM = "rem"        # Narrative synthesis


class PromotionReason(str, Enum):
    """Why a memory was promoted."""
    HIGH_IMPORTANCE = "high_importance"
    REPEATED_REFERENCE = "repeated_reference"
    USER_PREFERENCE = "user_preference"
    TASK_OUTCOME = "task_outcome"
    KNOWLEDGE_GAP = "knowledge_gap"
    EMOTIONAL_SIGNAL = "emotional_signal"


@dataclass
class MemoryCandidate:
    """A candidate memory staged for potential promotion."""
    content: str
    source_session: str = ""
    source_timestamp: float = 0.0
    importance_score: float = 0.0
    reference_count: int = 0
    memory_type: str = "episodic"
    reason: PromotionReason = PromotionReason.HIGH_IMPORTANCE
    content_hash: str = ""

    def __post_init__(self):
        if not self.content_hash:
            self.content_hash = hashlib.sha256(
                self.content.encode("utf-8")
            ).hexdigest()[:16]


@dataclass
class DreamingStatus:
    """Current dreaming state."""
    phase: DreamPhase = DreamPhase.IDLE
    last_run_at: float = 0.0
    next_run_at: float = 0.0
    total_runs: int = 0
    total_promoted: int = 0
    staged_count: int = 0
    error: str = ""


@dataclass
class DreamDiaryEntry:
    """A single dream diary entry."""
    date: str
    narrative: str
    promoted_count: int = 0
    themes: list[str] = field(default_factory=list)


class DreamingEngine:
    """
    Background memory consolidation inspired by OpenClaw Dreaming.

    Runs periodically (e.g., via cron) to:
    1. Extract important memories from recent sessions
    2. Promote them to durable MEMORY.md
    3. Generate narrative dream diary in DREAMS.md

    Usage:
        engine = DreamingEngine(memory_manager=mm, llm_client=llm)
        result = await engine.run_cycle()
    """

    def __init__(
        self,
        memory_manager: Any = None,
        llm_client: Any = None,
        promotion_threshold: float = 0.6,
        max_promotions_per_cycle: int = 10,
        recency_half_life_days: float = 7.0,
        max_age_days: float = 30.0,
    ) -> None:
        self._memory = memory_manager
        self._llm = llm_client
        self.promotion_threshold = promotion_threshold
        self.max_promotions = max_promotions_per_cycle
        self.recency_half_life = recency_half_life_days
        self.max_age_days = max_age_days
        self._status = DreamingStatus()
        self._staged: list[MemoryCandidate] = []
        self._promoted_hashes: set[str] = set()

        # Load existing promoted hashes to avoid duplicates
        self._load_promoted_hashes()

    def _load_promoted_hashes(self) -> None:
        """Load hashes of already-promoted memories from MEMORY.md."""
        if MEMORY_FILE.exists():
            content = MEMORY_FILE.read_text(encoding="utf-8")
            for line in content.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    h = hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]
                    self._promoted_hashes.add(h)

    @property
    def status(self) -> DreamingStatus:
        return self._status

    async def run_cycle(self) -> dict[str, Any]:
        """Run a complete dreaming cycle: light → deep → REM.

        Returns summary of what was done.
        """
        logger.info("dreaming_cycle_start")
        t0 = time.time()
        result: dict[str, Any] = {
            "started_at": t0, "phases": {}, "errors": [],
        }

        try:
            # Phase 1: Light Sleep — scan and stage
            self._status.phase = DreamPhase.LIGHT
            light_result = await self._light_sleep()
            result["phases"]["light"] = light_result

            # Phase 2: Deep Sleep — score and promote
            self._status.phase = DreamPhase.DEEP
            deep_result = await self._deep_sleep()
            result["phases"]["deep"] = deep_result

            # Phase 3: REM — narrative diary
            self._status.phase = DreamPhase.REM
            rem_result = await self._rem_sleep()
            result["phases"]["rem"] = rem_result

        except Exception as exc:
            result["errors"].append(str(exc))
            self._status.error = str(exc)
            logger.exception("dreaming_cycle_error")
        finally:
            self._status.phase = DreamPhase.IDLE
            self._status.last_run_at = time.time()
            self._status.total_runs += 1
            result["duration_s"] = round(time.time() - t0, 2)
            logger.info("dreaming_cycle_complete",
                        duration_s=result["duration_s"],
                        promoted=deep_result.get("promoted", 0) if "deep_result" in dir() else 0)

        return result

    # ── Phase 1: Light Sleep ────────────────────────────────────

    async def _light_sleep(self) -> dict[str, Any]:
        """Scan recent sessions and stage candidate memories."""
        candidates: list[MemoryCandidate] = []

        # Extract from recent session messages
        try:
            recent = await self._get_recent_sessions()
            for session in recent:
                extracted = await self._extract_candidates(session)
                candidates.extend(extracted)
        except Exception as exc:
            logger.warning("light_sleep_session_error", error=str(exc))

        # Deduplicate against already-promoted
        new_candidates = [
            c for c in candidates
            if c.content_hash not in self._promoted_hashes
        ]

        self._staged = new_candidates
        self._status.staged_count = len(new_candidates)

        logger.info("light_sleep_complete",
                     total_extracted=len(candidates),
                     new_staged=len(new_candidates))

        return {
            "total_extracted": len(candidates),
            "new_staged": len(new_candidates),
        }

    async def _get_recent_sessions(self) -> list[dict[str, Any]]:
        """Get recent session data for scanning."""
        try:
            from app.core.dependencies import _session_factory
            from app.models.database import ConversationMessageModel
            from sqlalchemy import select, desc
            from datetime import datetime, timedelta

            if not _session_factory:
                logger.debug("get_recent_sessions_skip", reason="db not initialized")
                return []

            cutoff = datetime.utcnow() - timedelta(days=self.max_age_days)

            async with _session_factory() as db:
                stmt = (
                    select(ConversationMessageModel)
                    .where(ConversationMessageModel.created_at >= cutoff)
                    .order_by(desc(ConversationMessageModel.created_at))
                    .limit(500)
                )
                result = await db.execute(stmt)
                rows = result.scalars().all()

                sessions: dict[str, list[dict]] = {}
                for row in rows:
                    sid = str(row.session_id)
                    if sid not in sessions:
                        sessions[sid] = []
                    sessions[sid].append({
                        "role": row.role,
                        "content": row.content,
                        "created_at": row.created_at.timestamp() if row.created_at else 0,
                    })

                return [{"session_id": k, "messages": v} for k, v in sessions.items()]
        except Exception as exc:
            logger.debug("get_recent_sessions_error", error=str(exc))
            return []

    async def _extract_candidates(
        self, session: dict[str, Any],
    ) -> list[MemoryCandidate]:
        """Extract candidate memories from a session."""
        candidates: list[MemoryCandidate] = []
        messages = session.get("messages", [])
        session_id = session.get("session_id", "")

        for msg in messages:
            content = msg.get("content", "")
            if not content or len(content) < 20:
                continue

            # Simple heuristic scoring
            score = self._score_message(content, msg.get("role", ""))
            if score >= self.promotion_threshold * 0.5:  # Lower threshold for staging
                candidates.append(MemoryCandidate(
                    content=content[:1000],
                    source_session=session_id,
                    source_timestamp=msg.get("created_at", 0),
                    importance_score=score,
                    memory_type="episodic",
                ))

        return candidates

    def _score_message(self, content: str, role: str) -> float:
        """Heuristic importance scoring for a message."""
        score = 0.3  # Base score

        # User preferences and instructions
        preference_signals = [
            "i prefer", "i like", "i want", "always use", "never use",
            "my style", "remember that", "important:", "note:",
            "tôi thích", "tôi muốn", "luôn luôn", "ghi nhớ",
        ]
        content_lower = content.lower()
        for signal in preference_signals:
            if signal in content_lower:
                score += 0.2
                break

        # Task outcomes (assistant messages with results)
        if role == "assistant" and any(w in content_lower for w in [
            "completed", "created", "generated", "found", "result:",
            "hoàn thành", "đã tạo", "kết quả",
        ]):
            score += 0.15

        # Longer content tends to be more informative
        if len(content) > 200:
            score += 0.1
        if len(content) > 500:
            score += 0.05

        # Code/technical content
        if "```" in content:
            score += 0.1

        # Cap at 1.0
        return min(score, 1.0)

    # ── Phase 2: Deep Sleep ─────────────────────────────────────

    async def _deep_sleep(self) -> dict[str, Any]:
        """Score staged candidates and promote top ones to MEMORY.md."""
        if not self._staged:
            return {"promoted": 0, "skipped": 0}

        # Sort by importance
        self._staged.sort(key=lambda c: c.importance_score, reverse=True)

        # Filter by threshold
        promotable = [
            c for c in self._staged
            if c.importance_score >= self.promotion_threshold
        ][:self.max_promotions]

        promoted = 0
        for candidate in promotable:
            try:
                await self._promote_memory(candidate)
                self._promoted_hashes.add(candidate.content_hash)
                promoted += 1
            except Exception as exc:
                logger.warning("promotion_failed", error=str(exc),
                               hash=candidate.content_hash)

        self._status.total_promoted += promoted
        logger.info("deep_sleep_complete",
                     promoted=promoted, candidates=len(self._staged))

        return {
            "promoted": promoted,
            "skipped": len(self._staged) - promoted,
            "threshold": self.promotion_threshold,
        }

    async def _promote_memory(self, candidate: MemoryCandidate) -> None:
        """Append a promoted memory to MEMORY.md."""
        MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Format entry
        from datetime import datetime
        date_str = datetime.fromtimestamp(candidate.source_timestamp).strftime("%Y-%m-%d") \
            if candidate.source_timestamp else datetime.utcnow().strftime("%Y-%m-%d")

        entry = f"\n- [{date_str}] ({candidate.reason.value}) {candidate.content[:500]}\n"

        # Append to MEMORY.md
        if MEMORY_FILE.exists():
            existing = MEMORY_FILE.read_text(encoding="utf-8")
        else:
            existing = "# Agent Memory\n\n_Auto-promoted by Dreaming Engine_\n"

        # Check for duplicate content
        if candidate.content[:100] in existing:
            logger.debug("memory_already_exists", hash=candidate.content_hash)
            return

        MEMORY_FILE.write_text(existing + entry, encoding="utf-8")
        logger.debug("memory_promoted", hash=candidate.content_hash,
                      score=candidate.importance_score)

    # ── Phase 3: REM Sleep ──────────────────────────────────────

    async def _rem_sleep(self) -> dict[str, Any]:
        """Generate narrative dream diary entry."""
        if not self._llm or not self._staged:
            return {"diary_written": False, "reason": "no LLM or no staged memories"}

        try:
            # Build context from staged memories
            context_parts = []
            for c in self._staged[:20]:
                context_parts.append(f"- {c.content[:200]}")
            context = "\n".join(context_parts)

            # Generate narrative
            resp = await self._llm.chat(
                messages=[{
                    "role": "system",
                    "content": (
                        "You are a reflective AI assistant writing a dream diary entry. "
                        "Summarize the day's conversations and discoveries in a poetic, "
                        "introspective style. Keep it to 3-4 paragraphs. "
                        "Include what was learned, patterns noticed, and insights gained."
                    ),
                }, {
                    "role": "user",
                    "content": f"Today's memory fragments:\n{context}"
                }],
                max_tokens=500,
                temperature=0.8,
            )

            narrative = resp.get("content", "")
            if not narrative:
                return {"diary_written": False, "reason": "empty LLM response"}

            # Write to DREAMS.md
            await self._write_dream_diary(narrative)

            return {"diary_written": True, "narrative_length": len(narrative)}

        except Exception as exc:
            logger.warning("rem_sleep_error", error=str(exc))
            return {"diary_written": False, "reason": str(exc)}

    async def _write_dream_diary(self, narrative: str) -> None:
        """Append a dream diary entry to DREAMS.md."""
        from datetime import datetime
        date_str = datetime.utcnow().strftime("%B %d, %Y, %I:%M %p UTC")

        entry = f"\n## {date_str}\n\n{narrative}\n\n---\n"

        DREAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if DREAMS_FILE.exists():
            existing = DREAMS_FILE.read_text(encoding="utf-8")
        else:
            existing = "# Dream Diary\n\n_Generated by MekongAI Nexus Dreaming Engine_\n"

        DREAMS_FILE.write_text(existing + entry, encoding="utf-8")
        logger.info("dream_diary_written", date=date_str)

    # ── Utility ─────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """Return current dreaming status."""
        return {
            "phase": self._status.phase.value,
            "last_run_at": self._status.last_run_at,
            "total_runs": self._status.total_runs,
            "total_promoted": self._status.total_promoted,
            "staged_count": self._status.staged_count,
            "error": self._status.error,
        }
