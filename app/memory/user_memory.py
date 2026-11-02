"""
Nexus - Persistent User Memory.

Implements the USER.md pattern from Hermes-agent: persistent per-user memory
that spans across all sessions. Stores user preferences, corrections,
discovered facts, and behavioral patterns.

Unlike session-specific context, user memory is GLOBAL and persists forever.
It's loaded into every conversation to personalize the agent's behavior.

Features:
- Key-value memory entries with categories
- Auto-extraction of preferences from conversations
- Cross-session persistence (DB-backed)
- Memory categories: preferences, facts, corrections, style
- Memory injection into system prompt
- Forgetting/updating existing memories
- Memory export for transparency (user can see what agent "knows")
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Configuration
MAX_MEMORY_ENTRIES_PER_USER = 200
MAX_MEMORY_INJECTION_CHARS = 3000  # Max chars to inject into system prompt
MEMORY_EXTRACTION_INTERVAL = 5  # Extract memories every N messages


class MemoryCategory(str, Enum):
    """Categories of user memory entries."""

    preference = "preference"  # User likes/dislikes, settings
    fact = "fact"  # Factual information about user (name, company, etc.)
    correction = "correction"  # Things user corrected the agent about
    style = "style"  # Communication style preferences
    context = "context"  # Background context (projects, goals)
    instruction = "instruction"  # Standing instructions from user


@dataclass
class MemoryEntry:
    """A single user memory entry."""

    key: str
    content: str
    category: MemoryCategory
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    source_session: str = ""  # Which session this was learned from
    confidence: float = 1.0  # 0-1, how confident we are about this
    times_used: int = 0  # How many times this was injected into context

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API/storage."""
        return {
            "key": self.key,
            "content": self.content,
            "category": self.category.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_session": self.source_session,
            "confidence": self.confidence,
            "times_used": self.times_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        """Deserialize from storage."""
        return cls(
            key=data["key"],
            content=data["content"],
            category=MemoryCategory(data.get("category", "fact")),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            source_session=data.get("source_session", ""),
            confidence=data.get("confidence", 1.0),
            times_used=data.get("times_used", 0),
        )


class UserMemory:
    """
    Persistent user memory manager.

    Stores and retrieves per-user memories that persist across sessions.
    Memories are categorized and prioritized for injection into context.

    Usage:
        memory = UserMemory(user_id="user123")
        await memory.load()  # Load from DB

        # Add memory
        await memory.add(
            key="preferred_language",
            content="User prefers responses in Vietnamese",
            category=MemoryCategory.preference,
        )

        # Get context injection for system prompt
        context = memory.get_injection_context()

        # Auto-extract from conversation
        await memory.extract_from_messages(messages, llm_client)
    """

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id
        self._entries: dict[str, MemoryEntry] = {}  # key → entry
        self._loaded = False
        self._dirty = False
        self._lock = asyncio.Lock()

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    async def load(self) -> None:
        """Load user memories from database."""
        try:
            from app.core.dependencies import _session_factory as _sf
            from app.models.database import ConversationMessageModel
            from sqlalchemy import select

            if not _sf:
                self._loaded = True
                return

            async with _sf() as db:
                # User memories stored as role='user_memory' messages
                # in a special session: f"__memory__{user_id}"
                memory_session_id = f"__memory__{self._user_id}"
                result = await db.execute(
                    select(ConversationMessageModel)
                    .where(
                        ConversationMessageModel.session_id == memory_session_id,
                        ConversationMessageModel.role == "user_memory",
                    )
                    .order_by(ConversationMessageModel.created_at.asc())
                )
                rows = result.scalars().all()

                for row in rows:
                    try:
                        import json
                        data = json.loads(row.content)
                        entry = MemoryEntry.from_dict(data)
                        self._entries[entry.key] = entry
                    except Exception:
                        continue

            self._loaded = True
            logger.debug(
                "user_memory_loaded",
                user_id=self._user_id,
                entries=len(self._entries),
            )

        except Exception as e:
            logger.warning(
                "user_memory_load_failed",
                user_id=self._user_id,
                error=str(e),
            )
            self._loaded = True  # Mark loaded to prevent repeated failures

    async def save(self) -> None:
        """Persist all memories to database."""
        if not self._dirty:
            return

        try:
            from app.core.dependencies import _session_factory as _sf
            from app.models.database import ConversationMessageModel
            from sqlalchemy import delete, select
            import json

            if not _sf:
                return

            async with _sf() as db:
                memory_session_id = f"__memory__{self._user_id}"

                # Delete all existing memory entries
                await db.execute(
                    delete(ConversationMessageModel).where(
                        ConversationMessageModel.session_id == memory_session_id,
                        ConversationMessageModel.role == "user_memory",
                    )
                )

                # Re-insert all current entries
                for entry in self._entries.values():
                    db.add(ConversationMessageModel(
                        session_id=memory_session_id,
                        role="user_memory",
                        content=json.dumps(entry.to_dict(), ensure_ascii=False),
                    ))

                await db.commit()

            self._dirty = False
            logger.debug(
                "user_memory_saved",
                user_id=self._user_id,
                entries=len(self._entries),
            )

        except Exception as e:
            logger.warning("user_memory_save_failed", user_id=self._user_id, error=str(e))

    async def add(
        self,
        key: str,
        content: str,
        category: MemoryCategory,
        *,
        source_session: str = "",
        confidence: float = 1.0,
    ) -> MemoryEntry:
        """
        Add or update a memory entry.

        If key already exists, updates the content and metadata.
        """
        async with self._lock:
            existing = self._entries.get(key)
            if existing:
                existing.content = content
                existing.updated_at = time.time()
                existing.confidence = max(existing.confidence, confidence)
                entry = existing
            else:
                # Enforce max entries
                if len(self._entries) >= MAX_MEMORY_ENTRIES_PER_USER:
                    self._evict_lowest_priority()

                entry = MemoryEntry(
                    key=key,
                    content=content,
                    category=category,
                    source_session=source_session,
                    confidence=confidence,
                )
                self._entries[key] = entry

            self._dirty = True

        # Auto-save
        await self.save()
        return entry

    async def remove(self, key: str) -> bool:
        """Remove a memory entry by key."""
        async with self._lock:
            if key in self._entries:
                del self._entries[key]
                self._dirty = True
                await self.save()
                return True
        return False

    def get(self, key: str) -> Optional[MemoryEntry]:
        """Get a specific memory entry."""
        return self._entries.get(key)

    def get_by_category(self, category: MemoryCategory) -> list[MemoryEntry]:
        """Get all entries in a category."""
        return [
            e for e in self._entries.values()
            if e.category == category
        ]

    def get_all(self) -> list[MemoryEntry]:
        """Get all memory entries."""
        return list(self._entries.values())

    def get_injection_context(self) -> str:
        """
        Generate context string to inject into system prompt.

        Prioritizes high-confidence, frequently-used entries.
        Formats as a USER.md-style context block.
        """
        if not self._entries:
            return ""

        # Sort by priority: confidence × (1 + times_used/10)
        sorted_entries = sorted(
            self._entries.values(),
            key=lambda e: e.confidence * (1 + e.times_used / 10),
            reverse=True,
        )

        # Build injection context
        lines: list[str] = ["[USER MEMORY — Persistent facts about this user]"]

        total_chars = 0
        categories_seen: dict[str, list[str]] = {}

        for entry in sorted_entries:
            line = f"- [{entry.category.value}] {entry.content}"
            if total_chars + len(line) > MAX_MEMORY_INJECTION_CHARS:
                break

            cat = entry.category.value
            if cat not in categories_seen:
                categories_seen[cat] = []
            categories_seen[cat].append(entry.content)
            total_chars += len(line) + 1
            entry.times_used += 1

        # Format by category for readability
        for cat, items in categories_seen.items():
            lines.append(f"\n## {cat.title()}")
            for item in items:
                lines.append(f"- {item}")

        lines.append("\n[END USER MEMORY]")
        return "\n".join(lines)

    async def extract_from_messages(
        self,
        messages: list[dict[str, Any]],
        llm_client: Any,
        *,
        session_id: str = "",
    ) -> list[MemoryEntry]:
        """
        Auto-extract memorable facts from a conversation.

        Uses LLM to identify:
        - User preferences expressed
        - Corrections user made
        - Facts about the user
        - Communication style cues

        Args:
            messages: Recent conversation messages.
            llm_client: LLM client for extraction.
            session_id: Current session ID (for source tracking).

        Returns:
            List of newly extracted memory entries.
        """
        # Only process if there are enough messages
        if len(messages) < 3:
            return []

        # Build conversation text for extraction
        conv_text = self._build_extraction_input(messages)
        if len(conv_text) < 50:
            return []

        try:
            extracted = await asyncio.wait_for(
                self._llm_extract_memories(conv_text, llm_client),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.debug("memory_extraction_timeout")
            return []
        except Exception as e:
            logger.debug("memory_extraction_failed", error=str(e))
            return []

        # Add extracted entries
        new_entries: list[MemoryEntry] = []
        for item in extracted:
            key = item.get("key", "")
            content = item.get("content", "")
            category_str = item.get("category", "fact")

            if not key or not content:
                continue

            try:
                category = MemoryCategory(category_str)
            except ValueError:
                category = MemoryCategory.fact

            entry = await self.add(
                key=key,
                content=content,
                category=category,
                source_session=session_id,
                confidence=item.get("confidence", 0.8),
            )
            new_entries.append(entry)

        if new_entries:
            logger.info(
                "user_memory_extracted",
                user_id=self._user_id,
                new_entries=len(new_entries),
                session_id=session_id,
            )

        return new_entries

    def _build_extraction_input(self, messages: list[dict[str, Any]]) -> str:
        """Build text for memory extraction from messages."""
        parts: list[str] = []
        for msg in messages[-10:]:  # Last 10 messages
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                parts.append(f"{role}: {content[:500]}")
        return "\n".join(parts)

    async def _llm_extract_memories(
        self,
        conversation_text: str,
        llm_client: Any,
    ) -> list[dict[str, Any]]:
        """Use LLM to extract memorable facts from conversation."""
        import json

        existing_keys = list(self._entries.keys())
        existing_context = (
            f"Already known: {', '.join(existing_keys[:20])}"
            if existing_keys
            else "No existing memories yet."
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "Extract persistent user memories from this conversation. "
                    "Look for:\n"
                    "1. PREFERENCES: Language, format, communication style preferences\n"
                    "2. FACTS: Name, company, role, location, projects\n"
                    "3. CORRECTIONS: Things the user corrected you about\n"
                    "4. INSTRUCTIONS: Standing orders/preferences for all conversations\n\n"
                    "Return a JSON array of objects with keys: "
                    "{key, content, category, confidence}\n"
                    "- key: short snake_case identifier\n"
                    "- content: one-sentence description\n"
                    "- category: preference|fact|correction|style|context|instruction\n"
                    "- confidence: 0.0-1.0\n\n"
                    f"{existing_context}\n\n"
                    "Only extract NEW information not already known. "
                    "Return [] if nothing new to learn. "
                    "Return ONLY the JSON array, no other text."
                ),
            },
            {
                "role": "user",
                "content": conversation_text[:3000],
            },
        ]

        result = await llm_client.chat(
            messages=messages,
            temperature=0.2,
            max_tokens=1000,
        )

        content = (result.get("content") or "").strip()

        # Parse JSON
        try:
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content)
        except (json.JSONDecodeError, IndexError):
            # Try to find JSON array in content
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start:end])
                except json.JSONDecodeError:
                    pass
        return []

    def _evict_lowest_priority(self) -> None:
        """Remove the lowest priority entry to make room."""
        if not self._entries:
            return

        # Lowest priority: lowest confidence × oldest × least used
        worst_key = min(
            self._entries.keys(),
            key=lambda k: (
                self._entries[k].confidence
                * (1 + self._entries[k].times_used / 10)
                / max(time.time() - self._entries[k].updated_at, 1)
            ),
        )
        del self._entries[worst_key]


# ── Module-level user memory cache ──

_user_memories: dict[str, UserMemory] = {}
_user_memories_lock = asyncio.Lock()


async def get_user_memory(user_id: str) -> UserMemory:
    """Get or create UserMemory for a user (cached, auto-loads from DB)."""
    async with _user_memories_lock:
        if user_id not in _user_memories:
            memory = UserMemory(user_id)
            await memory.load()
            _user_memories[user_id] = memory
        return _user_memories[user_id]


def get_cached_user_memory(user_id: str) -> Optional[UserMemory]:
    """Get cached UserMemory (non-async, returns None if not loaded yet)."""
    return _user_memories.get(user_id)
