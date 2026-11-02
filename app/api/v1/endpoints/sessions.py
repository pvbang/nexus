"""
Nexus - Session Search API.

Full-text search across conversation history,
session listing with auto-generated titles, session deletion,
and structured action timeline / stats (Phase 7c).

Inspired by: hermes-agent session management
"""

from datetime import datetime
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.action_logger import get_action_logger
from app.core.dependencies import get_db
from app.core.session_search import SearchResult, get_session_search_engine

logger = structlog.get_logger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class SearchResultResponse(BaseModel):
    """A single search result."""

    session_id: str
    role: str
    content_snippet: str
    timestamp: datetime
    relevance_score: float


class SearchResponse(BaseModel):
    """Full-text search response."""

    query: str
    results: list[SearchResultResponse]
    total: int


class SessionResponse(BaseModel):
    """Session info with auto-generated title."""

    session_id: str
    title: str
    created_at: datetime
    message_count: int = 0


class IndexMessageRequest(BaseModel):
    """Request to index a message for search."""

    session_id: str = Field(..., description="Conversation session ID")
    role: str = Field(..., description="Message role: user, assistant, system, tool")
    content: str = Field(..., description="Message content")
    timestamp: Optional[datetime] = Field(None, description="Message timestamp (defaults to now)")


class ActionEntryResponse(BaseModel):
    """A single action in the session timeline."""

    session_id: str
    timestamp: str
    action_type: str
    agent_name: Optional[str] = None
    content: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    duration_ms: Optional[int] = None


class SessionStatsResponse(BaseModel):
    """Overall session statistics."""

    session_id: str
    total_actions: int = 0
    total_tool_calls: int = 0
    agents_involved: list[str] = Field(default_factory=list)
    action_types: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0
    first_action: Optional[str] = None
    last_action: Optional[str] = None


class ConversationMessageResponse(BaseModel):
    """A single conversation message."""

    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    metadata: dict[str, Any] | None = None


class UpdateTitleRequest(BaseModel):
    """Request to rename a session."""

    title: str = Field(..., min_length=1, max_length=500, description="New session title")


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/resume/{session_id}", summary="Resume a previous session with full context")
async def resume_session(
    session_id: str,
    include_compression: bool = Query(False, description="Include compression chain data"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Resume a previous conversation session.

    Loads full history + compression chain for seamless continuation.
    Called when user opens a previous conversation from the history browser.

    Returns:
    - Session metadata (title, timestamps, message count)
    - Full message history (paginated)
    - Compression chain state (if any compressions occurred)
    - Session state (active/idle)
    """
    from app.models.database import ConversationMessageModel
    from app.core.conversation_titles import get_or_generate_title

    # Verify session exists
    count_stmt = (
        select(ConversationMessageModel.id)
        .where(
            ConversationMessageModel.session_id == session_id,
            ConversationMessageModel.role.in_(["user", "assistant"]),
        )
        .limit(1)
    )
    count_result = await db.execute(count_stmt)
    if not count_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    # Get title
    title = await get_or_generate_title(session_id)

    # Load recent messages (last 50 for immediate display)
    messages_stmt = (
        select(ConversationMessageModel)
        .where(
            ConversationMessageModel.session_id == session_id,
            ConversationMessageModel.role.in_(["user", "assistant"]),
        )
        .order_by(ConversationMessageModel.created_at.desc())
        .limit(50)
    )
    messages_result = await db.execute(messages_stmt)
    messages = list(reversed(messages_result.scalars().all()))

    # Get total message count
    total_stmt = (
        select(func.count(ConversationMessageModel.id))
        .where(
            ConversationMessageModel.session_id == session_id,
            ConversationMessageModel.role.in_(["user", "assistant"]),
        )
    )
    total_result = await db.execute(total_stmt)
    total_messages = total_result.scalar() or 0

    # Build response
    response: dict[str, Any] = {
        "session_id": session_id,
        "title": title or session_id[:12],
        "total_messages": total_messages,
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ],
        "has_more": total_messages > 50,
    }

    # Include compression chain if requested
    if include_compression:
        try:
            from app.core.compression_chain import get_or_create_chain
            chain = await get_or_create_chain(session_id)
            response["compression"] = {
                "chain": chain.get_full_chain(),
                "count": chain.compression_count,
                "context_limit": chain.context_limit,
                "threshold_tokens": chain.threshold_tokens,
            }
        except Exception:
            response["compression"] = {"chain": [], "count": 0}

    # Check if session has an active task
    try:
        from app.core.task_runner import get_task_runner
        runner = get_task_runner()
        active_task = runner.get_active_task(session_id)
        response["active_task"] = {
            "running": active_task is not None,
            "task_id": active_task.task_id if active_task else None,
        }
    except Exception:
        response["active_task"] = {"running": False, "task_id": None}

    logger.info(
        "session_resumed",
        session_id=session_id,
        total_messages=total_messages,
        has_compression=include_compression,
    )

    return response


@router.get("/search", summary="Full-text search across conversations")
async def search_sessions(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, ge=1, le=100, description="Max results"),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    """Search across all conversation messages using full-text search."""
    engine = get_session_search_engine()
    results = await engine.search(db, query=q, limit=limit, session_id=session_id)

    return SearchResponse(
        query=q,
        results=[
            SearchResultResponse(
                session_id=r.session_id,
                role=r.role,
                content_snippet=r.content_snippet,
                timestamp=r.timestamp,
                relevance_score=r.relevance_score,
            )
            for r in results
        ],
        total=len(results),
    )


@router.get("/", summary="List sessions with auto-generated titles")
async def list_sessions(
    limit: int = Query(50, ge=1, le=200, description="Max sessions to return"),
    db: AsyncSession = Depends(get_db),
) -> list[SessionResponse]:
    """Return all sessions with titles generated from the first user message."""
    engine = get_session_search_engine()
    titles = await engine.get_session_titles(db, limit=limit)

    return [
        SessionResponse(
            session_id=t.session_id,
            title=t.title,
            created_at=t.created_at,
            message_count=t.message_count,
        )
        for t in titles
    ]


@router.delete("/{session_id}", summary="Delete a session's search index")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Delete all indexed messages for a session."""
    engine = get_session_search_engine()
    count = await engine.delete_session_index(db, session_id=session_id)

    if count == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found or already empty",
        )

    return {
        "status": "deleted",
        "session_id": session_id,
        "messages_deleted": count,
    }


@router.post("/index", summary="Index a message for search")
async def index_message(
    request: IndexMessageRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Index a new message into the search engine."""
    engine = get_session_search_engine()
    await engine.index_message(
        db=db,
        session_id=request.session_id,
        role=request.role,
        content=request.content,
        timestamp=request.timestamp,
    )

    return {
        "status": "indexed",
        "session_id": request.session_id,
        "role": request.role,
        "content_length": len(request.content),
    }


# ------------------------------------------------------------------
# Conversation Messages (Phase 8 - Full Frontend)
# ------------------------------------------------------------------


@router.get(
    "/{session_id}/messages",
    summary="Load conversation messages for a session",
    response_model=list[ConversationMessageResponse],
)
async def get_session_messages(
    session_id: str,
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    limit: int = Query(200, ge=1, le=1000, description="Max messages to return"),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationMessageResponse]:
    """
    Return all conversation messages for a specific session.

    Messages are ordered chronologically (oldest first) for chat display.
    Supports pagination via offset/limit.
    """
    from sqlalchemy import select as sa_select
    from app.models.database import ConversationMessageModel

    try:
        stmt = (
            sa_select(ConversationMessageModel)
            .where(ConversationMessageModel.session_id == session_id)
            .order_by(ConversationMessageModel.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()
    except Exception as exc:
        logger.error("get_session_messages_query_error", error=str(exc), session_id=session_id)
        raise HTTPException(status_code=500, detail=f"DB query error: {exc}")

    # Return empty list for sessions with no messages yet (new sessions)
    # Frontend creates session client-side before first message
    if not messages:
        return []

    return [
        ConversationMessageResponse(
            id=str(m.id),
            session_id=m.session_id,
            role=m.role,
            content=m.content,
            created_at=m.created_at,
            metadata=m.metadata_ if hasattr(m, 'metadata_') else None,
        )
        for m in messages
    ]


@router.patch(
    "/{session_id}/title",
    summary="Update session title",
)
async def update_session_title(
    session_id: str,
    request: UpdateTitleRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Update the title of a session.

    Since titles are auto-generated from the first user message,
    this stores a custom title override by updating the first user
    message's content prefix with a title marker.
    For simplicity, we store the custom title in a lightweight
    metadata approach: insert a system message with title marker.
    """
    from sqlalchemy import select as sa_select
    from app.models.database import ConversationMessageModel

    # Verify session exists
    check = await db.execute(
        sa_select(ConversationMessageModel.id)
        .where(ConversationMessageModel.session_id == session_id)
        .limit(1)
    )
    if not check.scalar_one_or_none():
        raise HTTPException(
            status_code=404,
            detail=f"Session '{session_id}' not found",
        )

    # Upsert a title marker message (role='title')
    existing_title = await db.execute(
        sa_select(ConversationMessageModel)
        .where(
            ConversationMessageModel.session_id == session_id,
            ConversationMessageModel.role == "title",
        )
        .limit(1)
    )
    title_msg = existing_title.scalar_one_or_none()

    if title_msg:
        title_msg.content = request.title
    else:
        title_msg = ConversationMessageModel(
            session_id=session_id,
            role="title",
            content=request.title,
        )
        db.add(title_msg)

    await db.commit()

    return {
        "status": "updated",
        "session_id": session_id,
        "title": request.title,
    }


# ------------------------------------------------------------------
# Action Timeline & Stats (Phase 7c)
# ------------------------------------------------------------------


@router.get(
    "/{session_id}/timeline",
    summary="Get action timeline for a session",
    response_model=list[ActionEntryResponse],
)
async def get_session_timeline(
    session_id: str,
    action_type: Optional[str] = Query(None, description="Filter by action type"),
    agent: Optional[str] = Query(None, description="Filter by agent name"),
    db: AsyncSession = Depends(get_db),
) -> list[ActionEntryResponse]:
    """
    Return the chronological timeline of all actions in a session.

    Actions include: tool_call, agent_response, swarm_round, user_input, system_event.
    Falls back to persistent task_events table when in-memory log is empty.
    Returns empty list (not 404) when no actions exist.
    """
    action_logger = get_action_logger()

    if action_type or agent:
        entries = action_logger.search_actions(
            session_id=session_id,
            action_type=action_type,
            agent=agent,
        )
    else:
        entries = action_logger.get_session_timeline(session_id)

    if entries:
        return [
            ActionEntryResponse(
                session_id=e.session_id,
                timestamp=e.timestamp,
                action_type=e.action_type,
                agent_name=e.agent_name,
                content=e.content,
                details=e.details,
                duration_ms=e.duration_ms,
            )
            for e in entries
        ]

    # Fallback: query persistent task_events from DB
    from app.models.database import TaskEventModel

    stmt = (
        select(TaskEventModel)
        .where(TaskEventModel.session_id == session_id)
        .order_by(
            TaskEventModel.sequence.asc().nullslast(),
            TaskEventModel.created_at.asc(),
            TaskEventModel.id.asc(),
        )
    )
    result = await db.execute(stmt)
    task_events = result.scalars().all()

    if not task_events:
        return []  # Return empty list instead of 404

    return [
        ActionEntryResponse(
            session_id=session_id,
            timestamp=e.created_at.isoformat() if e.created_at else "",
            action_type=e.event_type,
            agent_name=(e.data or {}).get("agent_name"),
            content=(e.data or {}).get("title", ""),
            details=e.data or {},
        )
        for e in task_events
    ]


@router.get(
    "/{session_id}/stats",
    summary="Get session statistics",
    response_model=SessionStatsResponse,
)
async def get_session_stats(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> SessionStatsResponse:
    """
    Return aggregate statistics for a session.

    Includes total actions, tool calls, agents involved, and duration.
    Returns empty stats (not 404) when no actions exist.
    """
    action_logger = get_action_logger()
    summary = action_logger.get_session_summary(session_id)

    if summary["total_actions"] == 0:
        # Return empty stats instead of 404
        return SessionStatsResponse(
            session_id=session_id,
            total_actions=0,
        )

    return SessionStatsResponse(
        session_id=summary["session_id"],
        total_actions=summary["total_actions"],
        total_tool_calls=summary["total_tool_calls"],
        agents_involved=summary["agents_involved"],
        action_types=summary["action_types"],
        duration_ms=summary["duration_ms"],
        first_action=summary["first_action"],
        last_action=summary["last_action"],
    )


# ------------------------------------------------------------------
# Export (server-side Markdown/JSON download)
# ------------------------------------------------------------------


@router.get("/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = Query("markdown", pattern="^(markdown|json)$"),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Export a conversation session as Markdown or JSON.

    Returns downloadable content with appropriate Content-Disposition header.
    """
    from fastapi.responses import PlainTextResponse, JSONResponse
    from app.models.database import ConversationMessageModel

    # Fetch messages
    result = await db.execute(
        select(ConversationMessageModel)
        .where(ConversationMessageModel.session_id == session_id)
        .order_by(ConversationMessageModel.created_at.asc())
    )
    messages = result.scalars().all()

    if not messages:
        raise HTTPException(status_code=404, detail="Session not found or empty")

    # Get session title from first title message or first user message
    title = session_id
    for msg in messages:
        if msg.role == "title":
            title = msg.content
            break
        if msg.role == "user" and title == session_id:
            title = msg.content[:80]
            break

    if format == "json":
        export_data = {
            "session_id": session_id,
            "title": title,
            "exported_at": datetime.now().isoformat(),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in messages
                if m.role != "title"
            ],
        }
        return JSONResponse(
            content=export_data,
            headers={
                "Content-Disposition": f'attachment; filename="session-{session_id[:8]}.json"',
            },
        )

    # Markdown format
    lines = [
        f"# {title}\n",
        f"*Session ID: {session_id}*  ",
        f"*Exported: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n",
        "---\n",
    ]

    for msg in messages:
        if msg.role == "title":
            continue
        role_label = {"user": "👤 User", "assistant": "🤖 Assistant", "system": "⚙️ System"}.get(
            msg.role, msg.role
        )
        timestamp = msg.created_at.strftime("%H:%M") if msg.created_at else ""
        lines.append(f"### {role_label} {f'({timestamp})' if timestamp else ''}\n")
        lines.append(f"{msg.content}\n")
        lines.append("")

    markdown_content = "\n".join(lines)

    return PlainTextResponse(
        content=markdown_content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="session-{session_id[:8]}.md"',
        },
    )
