"""
Nexus - Session History Browser API.

Provides endpoints for browsing session history with preview, last_active,
search, and filtering capabilities. Designed for the frontend's session
sidebar/history panel.

Hermes-agent pattern: Full session history with search, enabling users to
quickly find and resume past conversations.

Features:
- List sessions with preview of last message
- Last activity timestamp per session
- Full-text search across all sessions
- Session metadata (message count, duration, agents used)
- Pagination support
- User memory management endpoints
"""

from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, distinct, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db

logger = structlog.get_logger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class SessionPreview(BaseModel):
    """Session with preview for history browser."""

    session_id: str
    title: str
    last_message_preview: str = ""
    last_active: Optional[datetime] = None
    created_at: Optional[datetime] = None
    message_count: int = 0
    has_tool_calls: bool = False
    agents_used: list[str] = Field(default_factory=list)


class SessionHistoryResponse(BaseModel):
    """Paginated session history response."""

    sessions: list[SessionPreview]
    total: int
    offset: int
    limit: int
    has_more: bool


class SessionSearchHit(BaseModel):
    """A search result in session history."""

    session_id: str
    title: str
    matched_content: str
    matched_role: str
    timestamp: Optional[datetime] = None
    relevance: float = 0.0


class SessionSearchResponse(BaseModel):
    """Session search response."""

    query: str
    hits: list[SessionSearchHit]
    total: int


class UserMemoryEntry(BaseModel):
    """A user memory entry for the API."""

    key: str
    content: str
    category: str
    confidence: float = 1.0
    created_at: Optional[float] = None
    updated_at: Optional[float] = None
    source_session: str = ""


class UserMemoryResponse(BaseModel):
    """User memory listing response."""

    user_id: str
    entries: list[UserMemoryEntry]
    total: int


class AddMemoryRequest(BaseModel):
    """Request to add a user memory."""

    key: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=1000)
    category: str = Field(default="fact", pattern="^(preference|fact|correction|style|context|instruction)$")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# ------------------------------------------------------------------
# Session History Endpoints
# ------------------------------------------------------------------


@router.get(
    "/history",
    summary="Browse session history with previews",
    response_model=SessionHistoryResponse,
)
async def browse_session_history(
    user_id: Optional[str] = Query(None, description="Filter by user ID"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    limit: int = Query(30, ge=1, le=100, description="Max sessions"),
    search: Optional[str] = Query(None, description="Search in titles/messages"),
    db: AsyncSession = Depends(get_db),
) -> SessionHistoryResponse:
    """
    Browse conversation session history with preview and metadata.

    Returns sessions sorted by last activity (most recent first).
    Each session includes: title, last message preview, timestamps,
    and message count.
    """
    from app.models.database import ConversationMessageModel

    try:
        # Subquery: get distinct session_ids with their first and last activity
        session_stats_subq = (
            select(
                ConversationMessageModel.session_id,
                func.count(ConversationMessageModel.id).label("msg_count"),
                func.min(ConversationMessageModel.created_at).label("first_at"),
                func.max(ConversationMessageModel.created_at).label("last_at"),
            )
            .where(
                ConversationMessageModel.role.in_(["user", "assistant"]),
                # Exclude internal memory sessions
                ~ConversationMessageModel.session_id.startswith("__memory__"),
            )
            .group_by(ConversationMessageModel.session_id)
        )

        # Apply search filter if provided
        if search:
            # Simple ILIKE search on content for session filtering
            matching_sessions_subq = (
                select(distinct(ConversationMessageModel.session_id))
                .where(
                    ConversationMessageModel.content.ilike(f"%{search}%"),
                    ~ConversationMessageModel.session_id.startswith("__memory__"),
                )
            )
            session_stats_subq = session_stats_subq.where(
                ConversationMessageModel.session_id.in_(matching_sessions_subq)
            )

        session_stats_subq = session_stats_subq.subquery()

        # Count total
        count_stmt = select(func.count()).select_from(session_stats_subq)
        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        # Fetch paginated sessions
        sessions_stmt = (
            select(session_stats_subq)
            .order_by(desc(session_stats_subq.c.last_at))
            .offset(offset)
            .limit(limit)
        )
        sessions_result = await db.execute(sessions_stmt)
        session_rows = sessions_result.all()

        # Build preview for each session
        previews: list[SessionPreview] = []
        for row in session_rows:
            session_id = row.session_id
            msg_count = row.msg_count
            first_at = row.first_at
            last_at = row.last_at

            # Get title (from 'title' role message or first user message)
            title = await _get_session_title(db, session_id)

            # Get last message preview
            last_msg_stmt = (
                select(ConversationMessageModel.content)
                .where(
                    ConversationMessageModel.session_id == session_id,
                    ConversationMessageModel.role.in_(["user", "assistant"]),
                )
                .order_by(ConversationMessageModel.created_at.desc())
                .limit(1)
            )
            last_msg_result = await db.execute(last_msg_stmt)
            last_msg = last_msg_result.scalar_one_or_none() or ""

            previews.append(SessionPreview(
                session_id=session_id,
                title=title,
                last_message_preview=last_msg[:150],
                last_active=last_at,
                created_at=first_at,
                message_count=msg_count,
            ))

        return SessionHistoryResponse(
            sessions=previews,
            total=total,
            offset=offset,
            limit=limit,
            has_more=(offset + limit) < total,
        )

    except Exception as e:
        logger.error("browse_history_error", error=str(e))
        raise HTTPException(status_code=500, detail=f"History query failed: {e}")


@router.get(
    "/history/search",
    summary="Full-text search across session history",
    response_model=SessionSearchResponse,
)
async def search_session_history(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    db: AsyncSession = Depends(get_db),
) -> SessionSearchResponse:
    """
    Full-text search across all session messages.

    Uses PostgreSQL tsvector when available, falls back to ILIKE.
    Returns matched content snippets with session context.
    """
    from app.models.database import ConversationMessageModel

    try:
        # Try tsvector search first (PostgreSQL)
        hits = await _tsvector_search(db, q, limit)
    except Exception:
        # Fallback to ILIKE
        hits = await _ilike_search(db, q, limit)

    return SessionSearchResponse(
        query=q,
        hits=hits,
        total=len(hits),
    )


@router.get(
    "/history/{session_id}",
    summary="Get full session details with compression chain",
)
async def get_session_detail(
    session_id: str,
    include_compression: bool = Query(False, description="Include compression chain data"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Get full details for a session including metadata and optional
    compression chain information (for session resume).
    """
    from app.models.database import ConversationMessageModel

    # Get message count and timestamps
    stats_stmt = select(
        func.count(ConversationMessageModel.id).label("msg_count"),
        func.min(ConversationMessageModel.created_at).label("first_at"),
        func.max(ConversationMessageModel.created_at).label("last_at"),
    ).where(
        ConversationMessageModel.session_id == session_id,
        ConversationMessageModel.role.in_(["user", "assistant"]),
    )
    stats_result = await db.execute(stats_stmt)
    stats = stats_result.one_or_none()

    if stats is None or stats.msg_count == 0:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    title = await _get_session_title(db, session_id)

    result: dict[str, Any] = {
        "session_id": session_id,
        "title": title,
        "message_count": stats.msg_count,
        "created_at": stats.first_at.isoformat() if stats.first_at else None,
        "last_active": stats.last_at.isoformat() if stats.last_at else None,
    }

    # Include compression chain if requested (for session resume)
    if include_compression:
        from app.core.compression_chain import get_or_create_chain
        from app.core.session_manager import make_session_key

        # Try to get existing chain state
        try:
            chain = await get_or_create_chain(session_id)
            result["compression_chain"] = chain.get_full_chain()
            result["compression_count"] = chain.compression_count
        except Exception:
            result["compression_chain"] = []
            result["compression_count"] = 0

    return result


# ------------------------------------------------------------------
# User Memory Endpoints
# ------------------------------------------------------------------


@router.get(
    "/memory/{user_id}",
    summary="Get user's persistent memory entries",
    response_model=UserMemoryResponse,
)
async def get_user_memories(
    user_id: str,
    category: Optional[str] = Query(None, description="Filter by category"),
) -> UserMemoryResponse:
    """Get all persistent memory entries for a user."""
    from app.memory.user_memory import get_user_memory, MemoryCategory

    memory = await get_user_memory(user_id)
    entries = memory.get_all()

    # Filter by category if specified
    if category:
        try:
            cat = MemoryCategory(category)
            entries = [e for e in entries if e.category == cat]
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category: {category}. "
                f"Valid: {[c.value for c in MemoryCategory]}",
            )

    return UserMemoryResponse(
        user_id=user_id,
        entries=[
            UserMemoryEntry(
                key=e.key,
                content=e.content,
                category=e.category.value,
                confidence=e.confidence,
                created_at=e.created_at,
                updated_at=e.updated_at,
                source_session=e.source_session,
            )
            for e in entries
        ],
        total=len(entries),
    )


@router.post(
    "/memory/{user_id}",
    summary="Add or update a user memory entry",
)
async def add_user_memory(
    user_id: str,
    request: AddMemoryRequest,
) -> dict[str, Any]:
    """Add or update a persistent memory entry for a user."""
    from app.memory.user_memory import get_user_memory, MemoryCategory

    memory = await get_user_memory(user_id)
    category = MemoryCategory(request.category)

    entry = await memory.add(
        key=request.key,
        content=request.content,
        category=category,
        confidence=request.confidence,
    )

    return {
        "status": "added",
        "key": entry.key,
        "content": entry.content,
        "category": entry.category.value,
    }


@router.delete(
    "/memory/{user_id}/{key}",
    summary="Delete a user memory entry",
)
async def delete_user_memory(
    user_id: str,
    key: str,
) -> dict[str, Any]:
    """Delete a specific memory entry for a user."""
    from app.memory.user_memory import get_user_memory

    memory = await get_user_memory(user_id)
    removed = await memory.remove(key)

    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Memory entry '{key}' not found for user '{user_id}'",
        )

    return {
        "status": "deleted",
        "key": key,
        "user_id": user_id,
    }


# ------------------------------------------------------------------
# Helper functions
# ------------------------------------------------------------------


async def _get_session_title(db: AsyncSession, session_id: str) -> str:
    """Get title for a session (from title message or first user message)."""
    from app.models.database import ConversationMessageModel

    # Check for explicit title message
    title_stmt = (
        select(ConversationMessageModel.content)
        .where(
            ConversationMessageModel.session_id == session_id,
            ConversationMessageModel.role == "title",
        )
        .limit(1)
    )
    title_result = await db.execute(title_stmt)
    title = title_result.scalar_one_or_none()

    if title:
        return title

    # Fallback: first user message truncated
    first_msg_stmt = (
        select(ConversationMessageModel.content)
        .where(
            ConversationMessageModel.session_id == session_id,
            ConversationMessageModel.role == "user",
        )
        .order_by(ConversationMessageModel.created_at.asc())
        .limit(1)
    )
    first_msg_result = await db.execute(first_msg_stmt)
    first_msg = first_msg_result.scalar_one_or_none()

    if first_msg:
        return first_msg[:80] + ("..." if len(first_msg) > 80 else "")

    return session_id[:12]


async def _tsvector_search(
    db: AsyncSession,
    query: str,
    limit: int,
) -> list[SessionSearchHit]:
    """Full-text search using PostgreSQL tsvector."""
    from sqlalchemy import text as sa_text

    sql = sa_text("""
        SELECT
            session_id,
            role,
            content,
            created_at,
            ts_rank(search_vector, plainto_tsquery(:query)) AS rank
        FROM conversation_messages
        WHERE search_vector @@ plainto_tsquery(:query)
            AND role IN ('user', 'assistant')
            AND session_id NOT LIKE '__memory__%'
        ORDER BY rank DESC
        LIMIT :limit
    """)

    result = await db.execute(sql, {"query": query, "limit": limit})
    rows = result.all()

    hits: list[SessionSearchHit] = []
    for row in rows:
        title = await _get_session_title(db, row.session_id)
        hits.append(SessionSearchHit(
            session_id=row.session_id,
            title=title,
            matched_content=row.content[:200],
            matched_role=row.role,
            timestamp=row.created_at,
            relevance=float(row.rank) if row.rank else 0.0,
        ))

    return hits


async def _ilike_search(
    db: AsyncSession,
    query: str,
    limit: int,
) -> list[SessionSearchHit]:
    """Fallback search using ILIKE (for non-PostgreSQL or missing tsvector)."""
    from app.models.database import ConversationMessageModel

    stmt = (
        select(ConversationMessageModel)
        .where(
            ConversationMessageModel.content.ilike(f"%{query}%"),
            ConversationMessageModel.role.in_(["user", "assistant"]),
            ~ConversationMessageModel.session_id.startswith("__memory__"),
        )
        .order_by(ConversationMessageModel.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    hits: list[SessionSearchHit] = []
    for row in rows:
        title = await _get_session_title(db, row.session_id)
        hits.append(SessionSearchHit(
            session_id=row.session_id,
            title=title,
            matched_content=row.content[:200],
            matched_role=row.role,
            timestamp=row.created_at,
            relevance=1.0,
        ))

    return hits
