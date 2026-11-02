"""
Nexus - Session State & Event Replay API.

Layer 2 & 3 of the Session Persistence fix:
- GET /sessions/{session_id}/state  → Current session state (is task running? what step?)
- GET /sessions/{session_id}/events → Replay missed events (for reconnect catch-up)

These endpoints enable the frontend to:
1. Know if a task is still running when page loads (F5 scenario)
2. Catch up on events missed during a WS disconnect
3. Show accurate progress indicators after reconnect
"""

from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_optional_user
from app.core.dependencies import get_db
from app.core.task_runner import get_task_runner
from app.models.database import TaskEventModel, TaskModel, ConversationMessageModel

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/sessions", tags=["sessions"])

_TERMINAL_DB_STATUSES = {"completed", "failed", "cancelled", "crashed"}
_TERMINAL_EVENT_TYPES = ("task_completed", "task_failed", "task_cancelled")
_STALE_HEARTBEAT_SECONDS = 90  # heartbeat loop runs every 10s; 9x margin


def decide_is_running(
    *,
    has_terminal_event: bool,
    memory_active: bool,
    db_status: Optional[str],
    heartbeat_idle_seconds: Optional[float],
    stale_threshold: float,
    recent_event_idle_seconds: Optional[float] = None,
) -> bool:
    """Single source of truth for whether a session's task is still running.

    Priority rules (first match wins):
    1. has_terminal_event → False. A persisted terminal event (task_completed/
       task_failed/task_cancelled) is the DEFINITIVE arbiter — it beats every
       other signal, including an in-memory task that lingers after finishing.
    2. memory_active → True. The in-memory runner still tracks this task.
    3. db_status non-terminal + a FRESH liveness signal → True. Backend likely
       restarted and evicted the task from memory while the DB row is still
       non-terminal. Two independent signals count as fresh: the heartbeat
       (best-effort, can miss writes under load) OR the most recent task_event
       (emitted by the orchestrator as it works — the stronger signal). The task
       is alive if EITHER is within the stale threshold, so a missed heartbeat on
       a busy task no longer reads as dead.
    4. Otherwise → False.
    """
    if has_terminal_event:
        return False
    if memory_active:
        return True
    if db_status is not None and db_status not in _TERMINAL_DB_STATUSES:
        for idle in (heartbeat_idle_seconds, recent_event_idle_seconds):
            if idle is not None and idle <= stale_threshold:
                return True
    return False


async def _validate_session_access(
    session_id: str, user_id: str, db: AsyncSession
) -> None:
    """Validate that user has access to this session.

    SEC-SESSION-01: Prevents cross-user data access (privacy leak).
    A user can only access sessions where they have messages or tasks.
    In dev mode (user_id="default_user"), all access is allowed.
    """
    if user_id == "default_user":
        return  # Dev mode — no auth restriction

    # Check if user owns any task in this session
    task_check = await db.execute(
        select(func.count())
        .select_from(TaskModel)
        .where(
            TaskModel.session_id == session_id,
            TaskModel.user_id == user_id,
        )
    )
    if (task_check.scalar() or 0) > 0:
        return

    # Check if user has any messages in this session
    msg_check = await db.execute(
        select(func.count())
        .select_from(ConversationMessageModel)
        .where(ConversationMessageModel.session_id == session_id)
    )
    # If there are messages but no task for user, still allow if session has no
    # explicit owner (legacy sessions). If session has tasks from other users, deny.
    msg_count = msg_check.scalar() or 0
    if msg_count > 0:
        other_user_tasks = await db.execute(
            select(func.count())
            .select_from(TaskModel)
            .where(
                TaskModel.session_id == session_id,
                TaskModel.user_id != user_id,
                TaskModel.user_id != "default_user",
            )
        )
        if (other_user_tasks.scalar() or 0) > 0:
            raise HTTPException(
                status_code=403,
                detail="Access denied: session belongs to another user",
            )
        return  # No conflicting owner — allow access

    # No tasks, no messages — session doesn't exist or empty (allow creation)
    return


@router.get("/{session_id}/state")
async def get_session_state(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_optional_user),
):
    """Get current state of a session — used on page load / reconnect.

    Returns whether a task is currently running, its progress,
    and the last event sequence number for catch-up.

    SEC-SESSION-01: Validates user has access to the requested session.

    Frontend flow on page load:
    1. Call this endpoint
    2. If is_running=True → subscribe to WS events + show progress
    3. If last_event_sequence > locally_stored_seq → call /events to catch up
    """
    await _validate_session_access(session_id, user_id, db)
    runner = get_task_runner()
    active_task = runner.get_active_task(session_id)
    last_task = runner.get_last_task(session_id)

    # Sequences restart at 0 per task, so MAX across all tasks is misleading.
    # Frontend uses this to decide if catch-up is needed — cross-task max causes
    # false positives (thinks it missed events when it didn't).
    latest_task_for_seq = await db.execute(
        select(TaskEventModel.task_id)
        .where(TaskEventModel.session_id == session_id)
        .order_by(TaskEventModel.created_at.desc())
        .limit(1)
    )
    _seq_task_id = latest_task_for_seq.scalar()
    if _seq_task_id:
        last_event_result = await db.execute(
            select(func.max(TaskEventModel.sequence))
            .where(
                TaskEventModel.session_id == session_id,
                TaskEventModel.task_id == _seq_task_id,
            )
        )
        last_seq = last_event_result.scalar() or 0
    else:
        last_seq = 0

    # Get most recent task from DB (for completed tasks)
    task_result = await db.execute(
        select(TaskModel)
        .where(TaskModel.session_id == session_id)
        .order_by(TaskModel.created_at.desc())
        .limit(1)
    )
    latest_db_task = task_result.scalar_one_or_none()

    # Get message count for this session
    msg_count_result = await db.execute(
        select(func.count())
        .select_from(ConversationMessageModel)
        .where(ConversationMessageModel.session_id == session_id)
    )
    message_count = msg_count_result.scalar() or 0

    # A persisted terminal event is the DEFINITIVE arbiter of is_running — it
    # beats an in-memory task that lingers after finishing and any non-terminal
    # DB row. Check the terminal event for the SPECIFIC task we are deciding
    # about: the in-memory active task if present (it is the most recent and may
    # have started AFTER _seq_task_id, before writing any event), otherwise the
    # latest task that has events (_seq_task_id) for the DB-fallback branch.
    # Using _seq_task_id unconditionally would let a just-completed older task's
    # terminal event wrongly mark a newly-started active task as not-running.
    terminal_check_task_id = active_task.task_id if active_task else _seq_task_id
    has_terminal_event = False
    if terminal_check_task_id:
        terminal_result = await db.execute(
            select(func.count())
            .select_from(TaskEventModel)
            .where(
                TaskEventModel.session_id == session_id,
                TaskEventModel.task_id == terminal_check_task_id,
                TaskEventModel.event_type.in_(_TERMINAL_EVENT_TYPES),
            )
        )
        has_terminal_event = (terminal_result.scalar() or 0) > 0

    # Determine status from active in-memory task or DB
    if active_task and not has_terminal_event:
        return {
            "session_id": session_id,
            "is_running": True,
            "task_id": active_task.task_id,
            "task_status": active_task.status,
            "started_at": active_task.started_at.isoformat(),
            "last_activity": active_task.last_activity.isoformat(),
            "last_activity_desc": active_task.last_activity_desc,
            "current_tool": active_task.current_tool,
            "last_event_sequence": last_seq,
            "message_count": message_count,
        }
    elif last_task and last_task.status in ("completed", "failed", "cancelled", "crashed"):
        return {
            "session_id": session_id,
            "is_running": False,
            "task_id": last_task.task_id,
            "task_status": last_task.status,
            "started_at": last_task.started_at.isoformat(),
            "last_activity": last_task.last_activity.isoformat(),
            "last_activity_desc": last_task.last_activity_desc,
            "current_tool": None,
            "last_event_sequence": last_seq,
            "message_count": message_count,
            "error": last_task.error,
        }
    else:
        # DB FALLBACK: The in-memory _running_tasks dict is empty for this session
        # (no active in-memory task, no recently-completed task either). This happens
        # when the backend restarted or evicted the task from memory WHILE the DB row
        # is still in a non-terminal status. Without this branch the endpoint would
        # always report is_running=False, causing the frontend to declare a still-live
        # task "completed". Trust the DB row, but only while its heartbeat is fresh —
        # a stale heartbeat means the worker died, so we let it read as not-running.
        # A persisted terminal event always overrides this (decide_is_running rule 1).
        db_status = latest_db_task.status if latest_db_task else None
        heartbeat_idle_seconds: Optional[float] = None
        recent_event_idle_seconds: Optional[float] = None
        if latest_db_task and db_status not in _TERMINAL_DB_STATUSES:
            now = datetime.now(timezone.utc)
            hb = latest_db_task.last_heartbeat
            ref = hb if hb is not None else latest_db_task.created_at
            if ref is not None:
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=timezone.utc)
                heartbeat_idle_seconds = (now - ref).total_seconds()

            # The most recent task_event is an independent — and stronger —
            # liveness signal than the heartbeat: it proves the orchestrator was
            # actively working. A busy task whose heartbeat write was dropped
            # (DB pool exhaustion / event-loop starvation) still reads as alive.
            last_event_at = await db.execute(
                select(func.max(TaskEventModel.created_at)).where(
                    TaskEventModel.task_id == latest_db_task.id
                )
            )
            latest_event = last_event_at.scalar()
            if latest_event is not None:
                if latest_event.tzinfo is None:
                    latest_event = latest_event.replace(tzinfo=timezone.utc)
                recent_event_idle_seconds = (now - latest_event).total_seconds()

        db_alive = decide_is_running(
            has_terminal_event=has_terminal_event,
            memory_active=False,
            db_status=db_status,
            heartbeat_idle_seconds=heartbeat_idle_seconds,
            stale_threshold=_STALE_HEARTBEAT_SECONDS,
            recent_event_idle_seconds=recent_event_idle_seconds,
        )

        if db_alive:
            logger.warning(
                "session_state.heartbeat_fallback",
                session_id=session_id,
                task_id=str(latest_db_task.id) if latest_db_task else None,
                db_status=db_status,
                heartbeat_idle_seconds=heartbeat_idle_seconds,
                recent_event_idle_seconds=recent_event_idle_seconds,
                reason="in-memory task absent; reporting is_running from fresh DB heartbeat or recent task_event",
            )

        return {
            "session_id": session_id,
            "is_running": db_alive,
            "task_id": str(latest_db_task.id) if latest_db_task else None,
            "task_status": (latest_db_task.status if latest_db_task else "idle") or "idle",
            "started_at": latest_db_task.created_at.isoformat() if latest_db_task else None,
            "last_activity": (
                latest_db_task.last_heartbeat.isoformat()
                if latest_db_task and latest_db_task.last_heartbeat else None
            ),
            "last_activity_desc": "running (recovered from DB)" if db_alive else None,
            "current_tool": None,
            "last_event_sequence": last_seq,
            "message_count": message_count,
        }


@router.get("/{session_id}/events")
async def get_session_events(
    session_id: str,
    after_seq: int = Query(0, ge=0, description="Return events after this sequence number"),
    limit: int = Query(100, ge=1, le=500, description="Max events to return"),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_optional_user),
):
    """Get task events for a session, optionally after a sequence number.

    Used by frontend to catch up on missed events after reconnect.

    SEC-SESSION-01: Validates user has access to the requested session.

    SEQUENCE+TASK_ID AWARE FIX: Sequences are PER-TASK (restart at 0 for each new task).
    Previous approach used `created_at > ref_timestamp` which could miss events when
    fire-and-forget persists reorder `created_at` relative to `sequence`.

    New approach:
    - Same task: use `sequence > after_seq` (immune to created_at reordering)
    - Cross-task: use `created_at > ref_created_at` to find newer tasks' events

    Flow:
    1. Client stores last_seen_sequence in localStorage
    2. On reconnect: GET /sessions/{id}/events?after_seq={last_seen}
    3. Replay returned events to update UI
    4. Subscribe to WS for live events going forward
    """
    await _validate_session_access(session_id, user_id, db)
    if after_seq > 0:
        # Then return: (same task, sequence > after_seq) UNION (newer tasks, all events)
        ref_result = await db.execute(
            select(TaskEventModel.task_id, TaskEventModel.created_at)
            .where(
                TaskEventModel.session_id == session_id,
                TaskEventModel.sequence == after_seq,
            )
            .order_by(TaskEventModel.created_at.desc())
            .limit(1)
        )
        ref_row = ref_result.one_or_none()

        if ref_row:
            ref_task_id, ref_created_at = ref_row
            # PLUS all events from NEWER tasks (cross-task scenario)
            query = (
                select(TaskEventModel)
                .where(
                    TaskEventModel.session_id == session_id,
                    or_(
                        # Same task — use sequence for correctness
                        and_(
                            TaskEventModel.task_id == ref_task_id,
                            TaskEventModel.sequence > after_seq,
                        ),
                        # Newer tasks — all their events
                        and_(
                            TaskEventModel.task_id != ref_task_id,
                            TaskEventModel.created_at > ref_created_at,
                        ),
                    )
                )
                .order_by(
                    TaskEventModel.created_at.asc(),
                    TaskEventModel.sequence.asc().nullslast(),
                    TaskEventModel.id.asc(),
                )
                .limit(limit)
            )
        else:
            # Reference event not found — return latest events from most recent task
            query = (
                select(TaskEventModel)
                .where(TaskEventModel.session_id == session_id)
                .order_by(
                    TaskEventModel.created_at.asc(),
                    TaskEventModel.sequence.asc().nullslast(),
                    TaskEventModel.id.asc(),
                )
                .limit(limit)
            )
    else:
        latest_task_result = await db.execute(
            select(TaskEventModel.task_id)
            .where(TaskEventModel.session_id == session_id)
            .order_by(TaskEventModel.created_at.desc())
            .limit(1)
        )
        latest_task_id = latest_task_result.scalar()

        if latest_task_id:
            query = (
                select(TaskEventModel)
                .where(
                    TaskEventModel.session_id == session_id,
                    TaskEventModel.task_id == latest_task_id,
                )
                .order_by(
                    TaskEventModel.created_at.asc(),
                    TaskEventModel.sequence.asc().nullslast(),
                    TaskEventModel.id.asc(),
                )
                .limit(limit)
            )
        else:
            # No events at all — empty result
            query = (
                select(TaskEventModel)
                .where(TaskEventModel.session_id == session_id)
                .order_by(
                    TaskEventModel.created_at.asc(),
                    TaskEventModel.sequence.asc().nullslast(),
                    TaskEventModel.id.asc(),
                )
                .limit(limit)
            )

    result = await db.execute(query)
    events = result.scalars().all()

    # Sort by started_at (initiation time) when available, fallback to sequence/created_at.
    event_list = []
    for e in events:
        data = e.data or {}
        started_at = data.get("started_at")
        event_list.append({
            "id": str(e.id),
            "task_id": str(e.task_id),
            "event_type": e.event_type,
            "data": data,
            "sequence": e.sequence,
            "created_at": e.created_at.isoformat(),
            "started_at": started_at,
        })

    event_list.sort(key=lambda ev: (ev["sequence"] or 0, 0))

    return {
        "session_id": session_id,
        "events": event_list,
        "count": len(event_list),
        "has_more": len(event_list) == limit,
        "after_seq": after_seq,
    }
