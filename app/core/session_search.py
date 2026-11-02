"""
Nexus - Session Search (Hermes-agent inspired).

Full-text search across conversation history.
Uses PostgreSQL tsvector/tsquery for production, fallback to in-memory for dev.

Inspired by: hermes-agent/hermes_state.py FTS5 search
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import structlog
from sqlalchemy import func, select, text, delete
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------


@dataclass
class SearchResult:
    """A single search result from conversation history."""

    session_id: str
    role: str
    content_snippet: str
    timestamp: datetime
    relevance_score: float = 0.0


@dataclass
class SessionTitle:
    """Auto-generated session title from first user message."""

    session_id: str
    title: str
    created_at: datetime
    message_count: int = 0


# ------------------------------------------------------------------
# Session Search Engine
# ------------------------------------------------------------------


class SessionSearchEngine:
    """
    Full-text search across conversation sessions.

    Production: PostgreSQL tsvector/tsquery with ts_rank()
    Dev fallback: simple LIKE-based search (in-memory for SQLite)
    """

    def __init__(self, use_postgres_fts: bool = True) -> None:
        self._use_postgres_fts = use_postgres_fts

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    async def index_message(
        self,
        db: AsyncSession,
        session_id: str,
        role: str,
        content: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Index a new message for search.

        Inserts into conversation_messages table. PostgreSQL will
        auto-populate the search_vector column via trigger or computed column.
        """
        from app.models.database import ConversationMessageModel

        ts = timestamp or datetime.utcnow()

        msg = ConversationMessageModel(
            session_id=session_id,
            role=role,
            content=content,
            created_at=ts,
        )
        db.add(msg)
        await db.flush()

        # Update search vector for PostgreSQL FTS
        if self._use_postgres_fts:
            try:
                await db.execute(
                    text(
                        "UPDATE conversation_messages "
                        "SET search_vector = to_tsvector('english', content) "
                        "WHERE id = :id"
                    ),
                    {"id": str(msg.id)},
                )
            except Exception as e:
                # Fallback silently — search_vector update is best-effort
                logger.debug("search_vector_update_failed", error=str(e))

        await db.commit()
        logger.debug(
            "message_indexed",
            session_id=session_id,
            role=role,
            content_len=len(content),
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        db: AsyncSession,
        query: str,
        limit: int = 20,
        session_id: Optional[str] = None,
    ) -> list[SearchResult]:
        """Full-text search across conversations.

        Uses PostgreSQL ts_rank for relevance scoring when available,
        falls back to LIKE search otherwise.
        """
        if not query or not query.strip():
            return []

        query = query.strip()

        if self._use_postgres_fts:
            return await self._search_postgres(db, query, limit, session_id)
        else:
            return await self._search_like(db, query, limit, session_id)

    async def _search_postgres(
        self,
        db: AsyncSession,
        query: str,
        limit: int,
        session_id: Optional[str],
    ) -> list[SearchResult]:
        """PostgreSQL full-text search with tsvector/tsquery."""
        from app.models.database import ConversationMessageModel

        # Use plainto_tsquery which safely handles natural language input
        # (no manual tsquery syntax needed — avoids special char parse errors)
        safe_query = query[:500]  # Limit length to avoid oversized tsquery

        try:
            sql = text(
                "SELECT id, session_id, role, content, created_at, "
                "ts_rank(search_vector, plainto_tsquery('english', :query)) AS rank "
                "FROM conversation_messages "
                "WHERE search_vector @@ plainto_tsquery('english', :query) "
                + ("AND session_id = :session_id " if session_id else "")
                + "ORDER BY rank DESC "
                "LIMIT :limit"
            )
            params: dict[str, Any] = {"query": safe_query, "limit": limit}
            if session_id:
                params["session_id"] = session_id

            result = await db.execute(sql, params)
            rows = result.fetchall()

            return [
                SearchResult(
                    session_id=row.session_id,
                    role=row.role,
                    content_snippet=_make_snippet(row.content, query),
                    timestamp=row.created_at,
                    relevance_score=float(row.rank),
                )
                for row in rows
            ]

        except Exception as e:
            logger.warning("postgres_fts_failed_falling_back", error=str(e))
            return await self._search_like(db, query, limit, session_id)

    async def _search_like(
        self,
        db: AsyncSession,
        query: str,
        limit: int,
        session_id: Optional[str],
    ) -> list[SearchResult]:
        """Simple LIKE-based search fallback."""
        from app.models.database import ConversationMessageModel

        stmt = (
            select(ConversationMessageModel)
            .where(ConversationMessageModel.content.ilike(f"%{query}%"))
        )
        if session_id:
            stmt = stmt.where(ConversationMessageModel.session_id == session_id)

        stmt = stmt.order_by(ConversationMessageModel.created_at.desc()).limit(limit)

        result = await db.execute(stmt)
        rows = result.scalars().all()

        return [
            SearchResult(
                session_id=row.session_id,
                role=row.role,
                content_snippet=_make_snippet(row.content, query),
                timestamp=row.created_at,
                relevance_score=1.0,  # no ranking for LIKE search
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Session Management
    # ------------------------------------------------------------------

    async def get_session_titles(
        self,
        db: AsyncSession,
        limit: int = 50,
    ) -> list[SessionTitle]:
        """Return session titles for the `limit` MOST RECENTLY ACTIVE sessions.

        ROOT-CAUSE FIX (history not updating / lost after reload): the previous
        query used `DISTINCT ON (session_id) ... ORDER BY session_id ASC LIMIT`.
        `DISTINCT ON` forces the leading ORDER BY column to be session_id, so the
        LIMIT truncated sessions by random UUID order — newly created conversations
        (and any session whose UUID sorted past the cutoff) could be permanently
        excluded from the list, vanishing after an F5 reload. It also gave no notion
        of recency, so a freshly-active old conversation never bubbled to the top.

        New approach (two stages):
          1. Rank sessions by last activity = MAX(created_at) per session, take top N.
          2. For those sessions, pull the FIRST user message as the title.
        The returned `created_at` is the LAST-activity timestamp so the frontend's
        recency sort and date grouping reflect actual recent usage.
        """
        from app.models.database import ConversationMessageModel

        # Stage 1: most-recently-active sessions (ranked by latest message time).
        activity_stmt = (
            select(
                ConversationMessageModel.session_id,
                func.max(ConversationMessageModel.created_at).label("last_activity"),
                func.count().label("message_count"),
            )
            .group_by(ConversationMessageModel.session_id)
            .order_by(func.max(ConversationMessageModel.created_at).desc())
            .limit(limit)
        )
        activity_rows = (await db.execute(activity_stmt)).all()
        if not activity_rows:
            return []

        session_ids = [r.session_id for r in activity_rows]
        last_activity_by_session = {r.session_id: r.last_activity for r in activity_rows}
        count_by_session = {r.session_id: r.message_count for r in activity_rows}

        # Stage 2: first user message per session (for the title) — scoped to the
        # already-selected top-N sessions via .in_() (portable expanding bind, no raw
        # array binding). .distinct(session_id) renders Postgres DISTINCT ON; the
        # leading order_by(session_id) it requires is satisfied below, and created_at
        # asc picks the FIRST user message per session.
        first_msg_stmt = (
            select(
                ConversationMessageModel.session_id,
                ConversationMessageModel.content,
            )
            .where(
                ConversationMessageModel.role == "user",
                ConversationMessageModel.session_id.in_(session_ids),
            )
            .distinct(ConversationMessageModel.session_id)
            .order_by(
                ConversationMessageModel.session_id,
                ConversationMessageModel.created_at.asc(),
            )
        )
        title_by_session: dict[str, str] = {}
        for row in (await db.execute(first_msg_stmt)).all():
            title_by_session[row.session_id] = _generate_title(row.content or "")

        # Assemble in recency order (Stage 1 already sorted by last_activity DESC).
        titles: list[SessionTitle] = []
        for sid in session_ids:
            titles.append(SessionTitle(
                session_id=sid,
                # Sessions with only non-user messages fall back to a default title.
                title=title_by_session.get(sid) or _generate_title(""),
                created_at=last_activity_by_session.get(sid) or datetime.utcnow(),
                message_count=count_by_session.get(sid, 0),
            ))

        return titles

    async def delete_session_index(
        self,
        db: AsyncSession,
        session_id: str,
    ) -> int:
        """Delete all indexed messages for a session. Returns count deleted."""
        from app.models.database import ConversationMessageModel

        result = await db.execute(
            delete(ConversationMessageModel).where(
                ConversationMessageModel.session_id == session_id
            )
        )
        await db.commit()
        count = result.rowcount or 0
        logger.info("session_index_deleted", session_id=session_id, count=count)
        return count


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_snippet(content: str, query: str, max_len: int = 200) -> str:
    """Extract a relevant snippet around the query match."""
    if not content:
        return ""

    # Find query position (case-insensitive)
    lower_content = content.lower()
    lower_query = query.lower()
    pos = lower_content.find(lower_query)

    if pos == -1:
        # No exact match, return beginning
        return content[:max_len] + ("..." if len(content) > max_len else "")

    # Extract window around match
    start = max(0, pos - 60)
    end = min(len(content), pos + len(query) + 60)

    snippet = content[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet


def _generate_title(content: str, max_len: int = 80) -> str:
    """Generate a session title from the first user message."""
    if not content:
        return "Untitled Session"

    # Clean up: remove extra whitespace, newlines
    title = re.sub(r"\s+", " ", content).strip()

    # Truncate intelligently at word boundary
    if len(title) > max_len:
        title = title[:max_len].rsplit(" ", 1)[0] + "..."

    return title or "Untitled Session"


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_engine: Optional[SessionSearchEngine] = None


def get_session_search_engine(use_postgres_fts: bool = True) -> SessionSearchEngine:
    """Get or create the global session search engine."""
    global _engine
    if _engine is None:
        _engine = SessionSearchEngine(use_postgres_fts=use_postgres_fts)
    return _engine
