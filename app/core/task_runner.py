"""
MekongAI Nexus - Background Task Runner.

UNIFIED execution engine for ALL chat paths (SSE + WebSocket).
Tasks run independently — surviving WS disconnect, page refresh, tab close.

Architecture (Simplified - May 2026):
- Task per session: Each session has at most ONE running task
- DB-first: ALL execution events persisted to DB immediately (fire-and-forget async)
- Frontend polls DB every 1s for events (single source of truth)
- WS subscribers: Only for live token streaming + status notifications
- Single persist path: ONLY TaskRunner persists assistant messages (no dual-persist)

Event flow:
  User Input → TaskRunner.start_task() → orchestrator runs in background
  → Each event: persist to DB immediately + push to WS subscribers
  → Frontend polls /sessions/{id}/events for display (works for ALL tabs/users)
  → Task completes: persist final message + emit task_completed
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

import structlog

logger = structlog.get_logger(__name__)

# Type alias for async event callback
EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


@dataclass
class RunningTask:
    """Represents a currently executing task in the background."""

    task_id: str
    session_id: str
    user_id: str
    message: str
    status: str = "running"  # running, completed, failed, cancelled
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_desc: str = "initializing"
    current_tool: Optional[str] = None
    asyncio_task: Optional[asyncio.Task] = field(default=None, repr=False)
    last_event_seq: int = 0
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    tokens_streamed: bool = False
    _terminal_emitted: bool = False
    collected_events: list[dict[str, Any]] = field(default_factory=list)
    _max_collected: int = field(default=50000, repr=False)
    use_free: bool = False  # Flag to use Kilo Gateway free models
    model_override: Optional[str] = None  # Optional model override


class TaskRunner:
    """Singleton that manages background task execution.

    UNIFIED architecture: Both SSE and WebSocket paths use start_task().
    This is the SINGLE source of truth for all running tasks.

    Each session can have at most one running task. New requests cancel
    the existing task before starting a new one.

    Subscribers (WS handlers, SSE generators) subscribe to events.
    If they disconnect, the task continues running. When they reconnect,
    they catch up via the Event Replay API.
    """

    MAX_TASKS_PER_USER = 5
    MAX_COMPLETED_CACHE = 200

    def __init__(self) -> None:
        self._running_tasks: dict[str, RunningTask] = {}  # session_id → RunningTask
        self._event_subscribers: dict[str, list[EventCallback]] = {}  # session_id → callbacks
        self._subscriber_failures: dict[int, int] = {}  # id(callback) → consecutive failure count
        from collections import OrderedDict
        self._completed_tasks: OrderedDict[str, RunningTask] = OrderedDict()
        self._lock = asyncio.Lock()
        # exhaustion under burst load (swarm debates can emit 20-50 events/second).
        # _pending_persists tracks active persist tasks for diagnostics.
        self._persist_semaphore = asyncio.Semaphore(5)
        self._pending_persists = 0  # Track queued persist tasks (diagnostic only)

    def get_active_task(self, session_id: str) -> Optional[RunningTask]:
        """Check if a session has an active running task."""
        task = self._running_tasks.get(session_id)
        if task and task.status == "running":
            return task
        return None

    def get_task_status(self, session_id: str) -> Optional[str]:
        """BUG-M1 FIX: Public method to check task status without direct dict access.

        Returns the status string ('running', 'completed', 'failed', 'cancelled')
        or None if no task exists for this session.
        """
        task = self._running_tasks.get(session_id)
        if task:
            return task.status
        return None

    def get_last_task(self, session_id: str) -> Optional[RunningTask]:
        """Get the most recent task for a session (running or completed)."""
        active = self._running_tasks.get(session_id)
        if active:
            return active
        return self._completed_tasks.get(session_id)

    def subscribe(self, session_id: str, callback: EventCallback) -> Callable[[], None]:
        """Subscribe to real-time events for a session.

        Returns an unsubscribe function. Call it when connection closes.
        """
        if session_id not in self._event_subscribers:
            self._event_subscribers[session_id] = []
        self._event_subscribers[session_id].append(callback)

        logger.debug("task_subscriber_added", session_id=session_id,
                     total_subs=len(self._event_subscribers[session_id]))

        def unsubscribe() -> None:
            if session_id in self._event_subscribers:
                self._event_subscribers[session_id] = [
                    cb for cb in self._event_subscribers[session_id] if cb != callback
                ]
                # on normal unsubscribe, only on failure-threshold removal.
                self._subscriber_failures.pop(id(callback), None)
                if not self._event_subscribers[session_id]:
                    del self._event_subscribers[session_id]

        return unsubscribe

    # Terminal event types that must NEVER be dropped (they signal end-of-task).
    _TERMINAL_EVENT_TYPES = frozenset({"task_completed", "task_failed", "task_cancelled"})

    def subscribe_queue(self, session_id: str) -> tuple[asyncio.Queue, Callable[[], None]]:
        """Subscribe to events via an asyncio.Queue (for SSE streaming).

        Returns (queue, unsubscribe_fn). The queue receives all events
        for this session. Call unsubscribe when the SSE connection closes.

        This is the preferred subscription method for SSE generators because
        it decouples event production (background task) from consumption (SSE yield).

        AUDIT-FIX: Terminal events (task_completed, task_failed, task_cancelled) are
        NEVER dropped, even when queue is full. Previously, if the queue was full,
        the oldest event was dropped — which could be a terminal event, leaving
        the SSE consumer stuck forever waiting for completion.

        PHASE-7 FIX: Increased queue size from 5000 → 20000 to reduce event drops
        during heavy swarm debates.

        BUG-M4 FIX: Pushes initial task status into queue immediately on subscribe.
        This ensures subscribers that connect AFTER a cancel/complete event was emitted
        still receive the terminal status (pull initial state, not just listen to events).
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=20000)

        async def _queue_pusher(event: dict[str, Any]) -> None:
            event_type = event.get("event_type", "")
            is_terminal = event_type in self._TERMINAL_EVENT_TYPES

            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                if is_terminal:
                    # by dropping the oldest NON-terminal event.
                    dropped = False
                    for _ in range(min(10, queue.qsize())):
                        try:
                            old_event = queue.get_nowait()
                            if old_event.get("event_type", "") not in self._TERMINAL_EVENT_TYPES:
                                dropped = True
                                break
                            else:
                                # Put terminal event back — can't drop it
                                queue.put_nowait(old_event)
                        except asyncio.QueueEmpty:
                            break
                    if not dropped:
                        # All events in queue are terminal (extremely unlikely) — just get oldest
                        try:
                            queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    queue.put_nowait(event)
                else:
                    # Non-terminal: drop oldest non-terminal event to make room
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(event)
                    except asyncio.QueueFull:
                        pass

        unsub = self.subscribe(session_id, _queue_pusher)

        # BUG-M4 FIX: Push current task status into queue immediately.
        # If a terminal event was already emitted before this subscribe call,
        # the new subscriber would miss it and hang forever.
        task = self._running_tasks.get(session_id)
        if task and task.status != "running":
            # Task already finished — push terminal status so SSE doesn't hang
            _status_event = {
                "event_type": f"task_{task.status}",
                "task_id": task.task_id,
                "session_id": session_id,
                "sequence": task.last_event_seq,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": {"reason": "status_on_subscribe"},
            }
            try:
                queue.put_nowait(_status_event)
            except asyncio.QueueFull:
                pass

        return queue, unsub

    # Consecutive failure threshold before removing subscriber.
    _SUBSCRIBER_FAILURE_THRESHOLD = 3

    async def _emit_event(self, session_id: str, event: dict[str, Any]) -> None:
        """Push event to all live subscribers of this session.

        Best-effort: failed sends are silently dropped (subscriber disconnected).
        Only removes subscribers after _SUBSCRIBER_FAILURE_THRESHOLD consecutive failures.

        CRIT-02 FIX: Snapshot subscriber list before asyncio.gather to prevent
        concurrent modification when a subscriber's callback triggers unsubscribe()
        (e.g., WS disconnect during event delivery). Post-gather comparison uses
        the snapshot for index stability.

        BUG-21 FIX: Also publish to Redis per-conversation channel for cross-instance
        delivery. Workers on other instances subscribe to this channel to relay events
        to their local WS clients.
        """
        subscribers = self._event_subscribers.get(session_id, [])
        if not subscribers:
            # BUG-21: Still publish to Redis even if no local subscribers
            # (event may be needed by WS clients on other workers)
            await self._publish_event_to_redis(session_id, event)
            return

        # If a subscriber calls unsubscribe() during gather (via its callback),
        # self._event_subscribers[session_id] mutates but our snapshot is stable.
        snapshot = list(subscribers)

        # Fan-out to all subscribers concurrently (using snapshot)
        results = await asyncio.gather(
            *(cb(event) for cb in snapshot),
            return_exceptions=True,
        )

        # Track consecutive failures per subscriber.
        # Compare against CURRENT list (may have changed during gather).
        dead_cbs: list[EventCallback] = []
        current_subs = self._event_subscribers.get(session_id, [])
        for i, result in enumerate(results):
            cb = snapshot[i]
            # Only act on callbacks still registered (not yet unsubscribed)
            if cb not in current_subs:
                # Already removed by unsubscribe() during gather — skip
                self._subscriber_failures.pop(id(cb), None)
                continue
            cb_id = id(cb)
            if isinstance(result, Exception):
                failures = self._subscriber_failures.get(cb_id, 0) + 1
                self._subscriber_failures[cb_id] = failures
                if failures >= self._SUBSCRIBER_FAILURE_THRESHOLD:
                    dead_cbs.append(cb)
            else:
                self._subscriber_failures.pop(cb_id, None)

        # Remove subscribers that exceeded failure threshold
        if dead_cbs:
            logger.debug("removing_dead_subscribers", session_id=session_id,
                         count=len(dead_cbs),
                         threshold=self._SUBSCRIBER_FAILURE_THRESHOLD)
            dead_set = set(id(cb) for cb in dead_cbs)
            for cb in dead_cbs:
                self._subscriber_failures.pop(id(cb), None)
            self._event_subscribers[session_id] = [
                cb for cb in current_subs if id(cb) not in dead_set
            ]
            if not self._event_subscribers[session_id]:
                del self._event_subscribers[session_id]

        # BUG-21: Publish to Redis for cross-instance delivery
        await self._publish_event_to_redis(session_id, event)

    async def _publish_event_to_redis(self, session_id: str, event: dict[str, Any]) -> None:
        """BUG-21 FIX: Publish event to Redis per-conversation channel.

        Enables cross-instance event delivery in multi-worker deployments.
        Workers on other instances subscribe to `nexus:chat:{session_id}` to
        relay events to their local WS clients.

        Best-effort: failures are logged but never crash the task.
        High-frequency token events are skipped to avoid flooding Redis.
        """
        # Skip high-frequency events (tokens) — they're only for direct WS subscribers
        event_type = event.get("event_type", "")
        if event_type == "token":
            return

        try:
            from app.communication.message_bus import get_message_bus
            bus = await get_message_bus()
            await bus.publish(
                f"nexus:chat:{session_id}",
                {**event, "_source_worker": id(self)},  # dedup marker
            )
        except Exception:
            pass  # Best-effort — fallback to in-memory only

    async def _ensure_task_record_upfront(self, running: RunningTask) -> None:
        """Persist TaskModel to DB synchronously before background execution.

        RACE-FIX: This ensures GET /tasks/{id} returns 200 immediately after
        task_started is broadcast to frontend. Previously, task creation was
        inside _execute_task() (background), causing 404s during the race window.

        Best-effort: If DB is unavailable, log warning and continue — the
        background task's _ensure_task_record() will retry.
        """
        from app.core.dependencies import _session_factory
        from app.models.database import TaskModel

        if not _session_factory:
            logger.warning("task_record_upfront_skip_no_db", task_id=running.task_id)
            return

        try:
            async with _session_factory() as db:
                new_task = TaskModel(
                    id=uuid.UUID(running.task_id),
                    title=running.message[:200] if running.message else "Background task",
                    status="executing",
                    user_id=running.user_id,
                    session_id=running.session_id,
                    last_heartbeat=datetime.now(timezone.utc),
                )
                db.add(new_task)
                await db.commit()
        except Exception as exc:
            # Non-fatal: background task's _ensure_task_record() will retry
            logger.warning(
                "task_record_upfront_failed",
                task_id=running.task_id,
                error=str(exc)[:200],
            )

    async def start_task(
        self,
        session_id: str,
        user_id: str,
        message: str,
        use_free: bool = False,
        model_override: Optional[str] = None,
    ) -> str:
        """Start orchestrator in background. Returns task_id.

        If a task is already running for this session, it's cancelled first.
        Used by BOTH SSE and WebSocket paths (unified execution).

        Args:
            session_id: Session identifier for the task.
            user_id: User requesting the task.
            message: The user's message to process.
            use_free: If True, use Kilo Gateway free models (anonymous, no API key required).
            model_override: Optional specific model to use.
        """
        # We can't call async _emit_event inside the lock without risking
        # deadlock if a subscriber callback tries to acquire the same lock.
        # Instead, capture info inside lock and emit after lock releases.
        _auto_cancelled_task: Optional[RunningTask] = None

        async with self._lock:
            # Per-user rate limit
            user_active_count = sum(
                1 for t in self._running_tasks.values()
                if t.user_id == user_id and t.status == "running"
            )
            if user_active_count >= self.MAX_TASKS_PER_USER:
                raise RuntimeError(
                    f"Too many concurrent tasks ({user_active_count}). "
                    f"Please wait for existing tasks to complete."
                )

            # Cancel existing task for this session — BUT protect long-running swarm tasks
            existing = self._running_tasks.get(session_id)
            if existing and existing.status == "running" and existing.asyncio_task:
                if not existing.asyncio_task.done():
                    # genuinely working. Only cancel simple chat tasks (< 30s old).
                    # Long-running tasks (swarm, debate) should only be cancelled explicitly.
                    _task_age_seconds = (
                        datetime.now(timezone.utc) - existing.started_at
                    ).total_seconds()
                    _is_long_running = _task_age_seconds > 30  # > 30s = likely a swarm/debate

                    if _is_long_running:
                        logger.warning(
                            "skipping_cancel_long_running_task",
                            session_id=session_id,
                            task_id=existing.task_id,
                            age_seconds=round(_task_age_seconds),
                            message="Long-running task protected from auto-cancel. Use explicit cancel.",
                        )
                        # Don't cancel — let it continue running
                        # The new message will be queued or rejected
                        raise RuntimeError(
                            "A task is still running in this session. "
                            "Please wait for it to complete or cancel it explicitly."
                        )
                    else:
                        logger.info("cancelling_existing_task",
                                    session_id=session_id,
                                    old_task_id=existing.task_id)
                        existing.asyncio_task.cancel()
                        existing.status = "cancelled"

                        # FIX: CRIT-01 - Wait for old task to actually finish before
                        # creating new one. Prevents old task's finally block from
                        # removing the NEW task from _running_tasks dict.
                        old_asyncio_task = existing.asyncio_task
                        if old_asyncio_task and not old_asyncio_task.done():
                            try:
                                await asyncio.wait_for(
                                    asyncio.shield(old_asyncio_task), timeout=20.0
                                )
                            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                                pass  # Best-effort wait

                        # would hang until stale timeout (300s) because no task_cancelled
                        # event was ever delivered to them.
                        _auto_cancelled_task = existing

            # Create new running task
            task_id = str(uuid.uuid4())
            running = RunningTask(
                task_id=task_id,
                session_id=session_id,
                user_id=user_id,
                message=message,
                use_free=use_free,
                model_override=model_override,
            )

            # asynchronously. Frontend receives task_id via SSE and immediately
            # starts polling GET /tasks/{id}. If DB persist was delayed (pool
            # exhaustion), frontend got 404 for minutes.
            await self._ensure_task_record_upfront(running)

            # Materialize any files the user uploaded in this conversation into
            # the task's _work/attachments/ so the agent can re-open the
            # originals at any time (summaries can be truncated/incomplete).
            self._materialize_uploads(task_id, session_id)

            # Create asyncio task INSIDE lock to prevent race
            running.asyncio_task = asyncio.create_task(
                self._execute_task(running),
                name=f"task-{task_id[:8]}",
            )
            self._running_tasks[session_id] = running

        # This notifies SSE/WS subscribers that the old task was cancelled,
        # allowing them to cleanly terminate instead of waiting for stale timeout.
        if _auto_cancelled_task:
            await self._update_task_db_status(
                _auto_cancelled_task.task_id, "cancelled"
            )
            await self._finalize_terminal(
                _auto_cancelled_task, "task_cancelled",
                {"reason": "auto_cancelled_by_new_message"},
            )

        logger.info("background_task_started",
                    task_id=task_id,
                    session_id=session_id,
                    user_id=user_id)

        # and initialize their processStore/executionStore to show progress.
        # Previously, task_started was only sent via the local WS event_pusher
        # to the initiating tab — other tabs/users never knew a task started.
        await self._broadcast_execution_event(
            "task_started",
            {
                "task_id": task_id,
                "session_id": session_id,
                "conversation_id": session_id,
                "user_id": user_id,
            },
            session_id,
            user_id=user_id,
        )

        return task_id

    async def resume_task(
        self,
        session_id: str,
        user_id: str,
        task_id: str,
        checkpoint: dict,
    ) -> str:
        """Resume a task from a checkpoint. Returns the same task_id.

        Loads checkpoint state and re-enters the execution pipeline
        from the turn where execution was interrupted.
        """
        message = checkpoint.get("state_json", {}).get("user_message", "")
        if not message:
            raise RuntimeError("Checkpoint has no user_message — cannot resume")

        async with self._lock:
            existing = self._running_tasks.get(session_id)
            if existing and existing.status == "running" and existing.asyncio_task:
                if not existing.asyncio_task.done():
                    raise RuntimeError(
                        "A task is already running in this session. "
                        "Cancel it first before resuming."
                    )

            running = RunningTask(
                task_id=task_id,
                session_id=session_id,
                user_id=user_id,
                message=message,
            )
            await self._ensure_task_record_upfront(running)

            running.asyncio_task = asyncio.create_task(
                self._execute_task(running, resume_checkpoint=checkpoint),
                name=f"task-resume-{task_id[:8]}",
            )
            self._running_tasks[session_id] = running

        logger.info(
            "task_resumed",
            task_id=task_id,
            session_id=session_id,
            resumed_from_turn=checkpoint.get("turn_number", 0),
        )

        await self._broadcast_execution_event(
            "task_resumed",
            {
                "task_id": task_id,
                "session_id": session_id,
                "conversation_id": session_id,
                "user_id": user_id,
                "resumed_from_turn": checkpoint.get("turn_number", 0),
            },
            session_id,
            user_id=user_id,
        )

        return task_id

    async def cancel_task(self, session_id: str) -> bool:
        """Cancel a running task for a session. Returns True if cancelled.

        PHASE-3 ENHANCED: Full cancellation that:
        1. Sets cancellation token (cooperative cancel for loops)
        2. Cancels asyncio task (force cancel for blocking operations)
        3. Updates DB status immediately (isolated session)
        4. Emits cancel event + broadcasts to all tabs
        5. Persists cancel event to DB (for replay on reconnect)
        6. Cleans up cancellation token and session state
        """
        # Acquire lock to safely read _running_tasks
        async with self._lock:
            task = self._running_tasks.get(session_id)
            if not (task and task.status == "running" and task.asyncio_task):
                return False
        # Lock released — proceed with cancellation on the copied reference
        if task and task.status == "running" and task.asyncio_task:
            # 1. Set cooperative cancellation token
            try:
                from app.core.cancellation import cancel_task as cancel_token_fn
                cancel_token_fn(task.task_id)
            except Exception:
                pass

            # 2. Cancel asyncio task (force)
            if not task.asyncio_task.done():
                task.asyncio_task.cancel()
            task.status = "cancelled"

            # 2b. Drain in-flight event persists so cancel event is not lost
            # FIX-8: Increased from 3s to 5s — heavy debates emit 20-50 events/sec
            # and 3s was insufficient to drain all pending persists on cancel.
            if self._pending_persists > 0:
                for _ in range(50):  # max 5s (50 × 100ms)
                    if self._pending_persists <= 0:
                        break
                    await asyncio.sleep(0.1)

            # FIX: CRIT-04 - Force reset stuck counter to prevent permanent block.
            # Counter can drift positive if persist task is GC'd without decrement.
            if self._pending_persists > 0:
                logger.warning(
                    "task_runner.cancel_drain_timeout",
                    session_id=session_id,
                    stuck_persists=self._pending_persists,
                )
                self._pending_persists = 0

            await self._update_task_db_status(task.task_id, "cancelled")

            # 4. Emit cancel event — persist-before-broadcast (no silent death).
            # Clients reconnecting after cancel must see the persisted terminal
            # event, else they think the task is still running.
            await self._finalize_terminal(task, "task_cancelled", {})

            # 6. Cleanup token and session state
            try:
                from app.core.cancellation import unregister_token, cleanup_session_state
                unregister_token(task.task_id)
                cleanup_session_state(session_id)
            except Exception:
                pass

            return True
        return False  # unreachable but keeps linter happy

    def _materialize_uploads(self, task_id: str, conversation_id: str) -> None:
        """Copy conversation uploads into the task's _work/attachments/.

        Best-effort and synchronous (cheap file copy) so the files are present
        before the agent's first turn. Never fails task creation.
        """
        try:
            from app.core import task_paths
            from app.services.document_processor import materialize_attachments

            work_dir = task_paths.work_dir(task_id)
            materialize_attachments(conversation_id, work_dir)
        except Exception as e:  # noqa: BLE001 — attachments are non-critical
            logger.warning("materialize_uploads_failed", task_id=task_id[:8], error=str(e)[:200])

    async def _update_task_db_status(
        self, task_id: str, status: str, *,
        error: Optional[str] = None,
        result_content: Optional[str] = None,
        respect_cancel: bool = False,
    ) -> None:
        """Update task status in DB using ISOLATED session (Phase 5).

        Never fails the calling operation — errors are logged and swallowed.
        Uses a fresh DB session independent of the orchestrator's session.

        Args:
            task_id: Task UUID string.
            status: New status (executing, completed, failed, cancelled).
            error: Error message (for failed tasks).
            result_content: Final answer content summary (for F5-recovery).
                           Stored in task.result JSON field so frontend can
                           retrieve it via GET /tasks/{id} when messages haven't
                           loaded yet.
        """
        from app.core.dependencies import _session_factory

        if not _session_factory:
            return

        try:
            from app.models.database import TaskModel
            from sqlalchemy import update as _upd

            async with _session_factory() as iso_db:
                # CANCEL-GUARD: a terminal 'completed'/'failed' write must not
                # clobber a cancellation that landed while the final turn drained.
                if respect_cancel and status not in ("cancelled",):
                    from app.core.cancel_signal import should_keep_cancelled
                    from sqlalchemy import select as _sel
                    row = await iso_db.execute(
                        _sel(TaskModel.status, TaskModel.metadata_).where(
                            TaskModel.id == uuid.UUID(task_id)
                        )
                    )
                    cur = row.first()
                    if cur is not None:
                        cur_status = cur[0] or ""
                        cur_meta = cur[1] or {}
                        _cancel_req = bool(cur_meta.get("cancel_requested"))
                        if should_keep_cancelled(cur_status, _cancel_req):
                            logger.info(
                                "task_status_overwrite_blocked",
                                task_id=task_id,
                                attempted=status,
                                kept="cancelled",
                            )
                            return
                values: dict[str, Any] = {"status": status}
                if error:
                    values["error"] = error
                await iso_db.execute(
                    _upd(TaskModel)
                    .where(TaskModel.id == uuid.UUID(task_id))
                    .values(**values)
                )
                # Frontend checks taskDetail.result?.content when assistant message
                # hasn't been loaded yet from conversation_messages table.
                # Orchestrator commits a FULL result (50KB content, tool_calls, metadata)
                # to task.result BEFORE this runs. Without the IS NULL guard, this
                # overwrites the full result with a 2KB truncated version — destroying
                # tool_calls count, content_length, and most of the response content.
                # The truncated fallback only kicks in when orchestrator's commit
                # failed (DB error, crash) and result is still NULL.
                if result_content:
                    await iso_db.execute(
                        _upd(TaskModel)
                        .where(TaskModel.id == uuid.UUID(task_id))
                        .where(TaskModel.result.is_(None))
                        .values(result={"content": result_content})
                    )
                await iso_db.commit()
        except Exception as exc:
            logger.warning(
                "task_db_status_update_failed",
                error=str(exc),
                task_id=task_id,
                target_status=status,
            )

    async def _execute_task(self, running: RunningTask, resume_checkpoint: dict | None = None) -> None:
        """The actual orchestrator execution — runs in background.

        This is the SINGLE execution path for ALL chat requests.
        Both SSE and WS paths delegate here. Subscribers receive events.

        Args:
            resume_checkpoint: If provided, pipeline resumes from this checkpoint
                instead of starting fresh.
        """
        from app.audit import get_or_create_audit_logger, remove_audit_logger
        from app.core.dependencies import _session_factory
        from app.models.database import TaskEventModel, TaskModel
        from app.services.orchestrator import CoreOrchestrator

        audit = get_or_create_audit_logger(
            session_id=running.session_id,
            user_id=running.user_id,
        )
        try:
            await audit.log_phase("task_execution", "start")
            await audit.log_agent_start("orchestrator", running.message)
        except Exception:
            pass

        if not _session_factory:
            running.status = "failed"
            running.error = "Database not initialized"
            running.last_event_seq += 1
            failure_event = {
                "event_type": "task_failed",
                "task_id": running.task_id,
                "session_id": running.session_id,
                "sequence": running.last_event_seq,
                "data": {"error": "Database not initialized"},
            }
            await self._emit_event(running.session_id, failure_event)
            try:
                await audit.log_error(RuntimeError("Database not initialized"), context="startup")
                await audit.log_summary()
                remove_audit_logger(running.session_id)
            except Exception:
                pass
            return

        try:
            async with _session_factory() as db:
                orchestrator = CoreOrchestrator(db)

                # Inject TaskRunner's task_id so orchestrator uses the SAME ID
                orchestrator._external_task_id = uuid.UUID(running.task_id)

                # ── IMMEDIATE EVENT PERSISTENCE ──
                # All execution events are persisted to DB immediately (non-blocking).
                # Frontend polls DB for events — this is the SINGLE SOURCE OF TRUTH.
                # No batch buffering; each event is written as it happens.
                _PERSIST_EVENT_TYPES = {
                    "tool_start", "tool_done", "agent_spawn",
                    "planning_start", "planning_done", "deep_analysis",
                    "deep_analysis_complete", "self_reflection", "auto_verify",
                    "completion_check", "mid_analysis", "debate_critique",
                    "debate_revision", "debate_proposal", "debate_rebuttal",
                    "debate_verdict", "xskill_retrieve", "xskill_accumulate",
                    "swarm_round", "swarm_round_start", "agent_response",
                    "agent_thinking", "step", "micro_step",
                    # Chat debate events (from chat_room.py)
                    "debate_started", "debate_message", "debate_tool_call",
                    "debate_tool_result", "debate_agent_plan", "debate_turn",
                    "debate_action_phase",
                    "moderator_intervention", "debate_phase", "debate_concluded",
                    "synthesis_start", "synthesis_done", "report_building",
                    "swarm_activated",
                    "debate_reflection", "debate_gate_check",
                    "action_tool_call", "action_tool_result",
                    "preparation_research", "preparation_started",
                    "preparation_plan", "preparation_complete",
                    # BUG-4 FIX: Added missing event types needed for frontend
                    # react_phase: shows execution phase progress (plan/act/observe/adapt/synthesize)
                    # debate_error: persists error state so frontend can display after reconnect
                    # context_compaction: shows when context was compressed (user visibility)
                    # context_trimmed: shows when context was trimmed at start
                    "react_phase", "debate_error", "context_compaction",
                    "context_trimmed",
                    # Event persistence gaps fix: these were only in ACTIVITY.md
                    "agent_stuck", "tool_error", "evaluation_done",
                    # Orchestrator coordination events
                    "agent_thinking", "next_turn_plan",
                    # Evolution Agent insight event
                    "evolution_insight",
                    # Document lifecycle events
                    "document_created",
                    # Agent lifecycle events (from agent_worker.py)
                    "agent_force_stopped", "agent_failed",
                    # Resume events
                    "task_resumed",
                    # Graph RAG indexing event (from pipeline.py)
                    "graph_rag_indexed",
                    # Orchestrator decision/insight events — previously emitted but
                    # NOT persisted, so they vanished on reload (only visible live).
                    # metacognitive_insight can carry a question for the user.
                    "metacognitive_insight", "authorization_required",
                    "divergent_thinking_done", "workspace_rollback",
                    "infrastructure_backoff",
                }
                _db_task_id_cache: list = []  # mutable container for closure

                async def _ensure_task_record() -> uuid.UUID:
                    """Ensure TaskModel exists in DB. Returns task_id UUID."""
                    if _db_task_id_cache:
                        return _db_task_id_cache[0]
                    from sqlalchemy import select as _sel
                    async with _session_factory() as init_db:
                        result_q = await init_db.execute(
                            _sel(TaskModel.id)
                            .where(TaskModel.id == uuid.UUID(running.task_id))
                            .limit(1)
                        )
                        existing_id = result_q.scalar()
                        if not existing_id:
                            new_task = TaskModel(
                                id=uuid.UUID(running.task_id),
                                title=running.message[:200] if running.message else "Background task",
                                status="executing",
                                user_id=running.user_id,
                                session_id=running.session_id,
                                # crashes even in first 10s before heartbeat loop fires
                                last_heartbeat=datetime.now(timezone.utc),
                            )
                            init_db.add(new_task)
                            await init_db.commit()
                            _db_task_id_cache.append(uuid.UUID(running.task_id))
                        else:
                            _db_task_id_cache.append(existing_id)
                    return _db_task_id_cache[0]

                # Create task record upfront
                await _ensure_task_record()

                async def _persist_event_immediate(enriched: dict[str, Any]) -> None:
                    """Persist a single event to DB immediately (fire-and-forget).

                    HIGH-3 FIX: Uses semaphore + pending counter to prevent pool exhaustion.
                    Tracks _pending_persists to detect backlog and drop low-priority events.

                    PIPELINE-AUDIT-C3 BUG-M2 FIX: Moved counter increment INSIDE the try
                    block to prevent counter drift if the task is cancelled between
                    increment and semaphore acquisition.

                    AUDIT-BUG-5 FIX: Catch CancelledError explicitly so counter is always
                    decremented even during graceful shutdown or task group cancellation.
                    Without this, orphaned persist tasks during shutdown could leave
                    _pending_persists permanently elevated, blocking future events.
                    """
                    try:
                        self._pending_persists += 1
                        async with self._persist_semaphore:
                            try:
                                db_task_id = _db_task_id_cache[0] if _db_task_id_cache else uuid.UUID(running.task_id)
                                _event_data = enriched.get("data") if "data" in enriched else {
                                    k: v for k, v in enriched.items()
                                    if k not in ("event_type", "sequence", "task_id",
                                                 "session_id", "conversation_id")
                                }
                                # so frontend can display the correct time (emit time, not DB insert time).
                                # Previously timestamp was excluded, causing events to show DB insert time.
                                if "timestamp" not in _event_data and "timestamp" in enriched:
                                    _event_data["timestamp"] = enriched["timestamp"]
                                async with _session_factory() as evt_db:
                                    evt_db.add(TaskEventModel(
                                        task_id=db_task_id,
                                        session_id=running.session_id,
                                        event_type=enriched.get("event_type", "unknown"),
                                        data=_event_data,
                                        sequence=enriched.get("sequence", 0),
                                    ))
                                    await evt_db.commit()
                            except asyncio.CancelledError:
                                # from fire-and-forget persist tasks. Just log and exit.
                                logger.debug("event_persist_cancelled",
                                             event_type=enriched.get("event_type"))
                            except Exception as exc:
                                logger.debug("event_persist_error", error=str(exc),
                                             event_type=enriched.get("event_type"))
                    except asyncio.CancelledError:
                        # while waiting for semaphore). Counter is still decremented in finally.
                        pass
                    finally:
                        self._pending_persists -= 1

                async def _event_bridge(event: dict[str, Any]) -> None:
                    """Bridge orchestrator events → DB + WS subscribers.

                    ALL important events are persisted to DB immediately.
                    Frontend polls DB for event list (single source of truth).
                    WS subscribers still get live push for token streaming.

                    PHASE-2 FIX: Preserves _source_timestamp from event source
                    for accurate event timing.
                    Falls back to datetime.now() only if source didn't provide timestamp.

                    PHASE-7 FIX: CRITICAL events (task_completed, task_failed, task_cancelled)
                    are persisted SYNCHRONOUSLY (await) — never fire-and-forget.
                    """
                    # Update activity tracking
                    running.last_activity = datetime.now(timezone.utc)
                    event_type = event.get("event_type", "unknown")
                    running.last_activity_desc = event_type
                    running.last_event_seq += 1

                    # Collect events for metadata (capped) — include key fields for history display
                    if len(running.collected_events) < running._max_collected:
                        _data = event.get("data", {})
                        _light_event: dict[str, Any] = {
                            "event_type": event_type,
                            "sequence": running.last_event_seq,
                        }
                        # Agent name/role (needed for display)
                        _agent_name = event.get("agent_name") or _data.get("agent_name")
                        if _agent_name:
                            _light_event["agent_name"] = str(_agent_name)
                        _agent_role = event.get("agent_role") or _data.get("agent_role")
                        if _agent_role:
                            _light_event["agent_role"] = str(_agent_role)
                        # Tool name
                        _tool = event.get("tool") or _data.get("tool")
                        if _tool:
                            _light_event["tool"] = str(_tool)
                        # Success/duration for tool_done
                        if event_type == "tool_done":
                            if "success" in event:
                                _light_event["success"] = event["success"]
                            elif "success" in _data:
                                _light_event["success"] = _data["success"]
                            _exec_time = event.get("execution_time_ms") or _data.get("execution_time_ms")
                            if _exec_time:
                                _light_event["execution_time_ms"] = _exec_time
                        # Content/preview for thinking events + debate messages (truncated)
                        if event_type in ("agent_thinking", "agent_response",
                                          "debate_message", "debate_proposal", "debate_rebuttal",
                                          "moderator_intervention"):
                            _content = event.get("content") or _data.get("content") or event.get("preview") or _data.get("preview") or event.get("message") or _data.get("message") or ""
                            if _content:
                                _light_event["content"] = str(_content)[:2000]
                        # Debate lifecycle events — save key metadata
                        if event_type in ("debate_started", "debate_concluded", "debate_phase",
                                          "synthesis_start", "synthesis_done", "swarm_activated"):
                            _content = event.get("status") or _data.get("status") or event.get("phase") or _data.get("phase") or ""
                            if _content:
                                _light_event["content"] = str(_content)[:500]
                            # Agents list for debate_started
                            _agents = event.get("agents") or _data.get("agents")
                            if _agents:
                                _light_event["agents"] = _agents[:10] if isinstance(_agents, list) else _agents
                            # Convergence for concluded
                            _conv = event.get("convergence_score") or _data.get("convergence_score")
                            if _conv is not None:
                                _light_event["convergence_score"] = _conv
                            _concluded_by = event.get("concluded_by") or _data.get("concluded_by")
                            if _concluded_by:
                                _light_event["concluded_by"] = str(_concluded_by)
                            _total_turns = event.get("total_turns") or _data.get("total_turns")
                            if _total_turns is not None:
                                _light_event["total_turns"] = _total_turns
                        # Moderator intervention metadata
                        if event_type == "moderator_intervention":
                            _action = event.get("action") or _data.get("action")
                            if _action:
                                _light_event["action"] = str(_action)
                            _quality = event.get("quality_score") or _data.get("quality_score")
                            if _quality is not None:
                                _light_event["quality_score"] = _quality
                            _convergence = event.get("convergence_score") or _data.get("convergence_score")
                            if _convergence is not None:
                                _light_event["convergence_score"] = _convergence
                        # Debate tool call metadata
                        if event_type in ("debate_tool_call", "action_tool_call"):
                            _dtool = event.get("tool") or _data.get("tool")
                            if _dtool:
                                _light_event["tool"] = str(_dtool)
                            _turn = event.get("turn") or _data.get("turn")
                            if _turn is not None:
                                _light_event["turn"] = _turn
                            _params = event.get("params_preview") or _data.get("params_preview")
                            if _params:
                                _light_event["params_preview"] = str(_params)[:200]
                        # Debate/action tool result metadata
                        if event_type in ("debate_tool_result", "action_tool_result"):
                            _dtool = event.get("tool") or _data.get("tool")
                            if _dtool:
                                _light_event["tool"] = str(_dtool)
                            _turn = event.get("turn") or _data.get("turn")
                            if _turn is not None:
                                _light_event["turn"] = _turn
                            _success = event.get("success") if event.get("success") is not None else _data.get("success")
                            if _success is not None:
                                _light_event["success"] = _success
                            _output = event.get("output_preview") or _data.get("output_preview")
                            if _output:
                                _light_event["output_preview"] = str(_output)[:300]
                        # Debate agent plan metadata
                        if event_type == "debate_agent_plan":
                            _arg = event.get("main_argument") or _data.get("main_argument")
                            if _arg:
                                _light_event["content"] = str(_arg)[:200]
                            _turn = event.get("turn") or _data.get("turn")
                            if _turn is not None:
                                _light_event["turn"] = _turn
                        # Debate turn start metadata
                        if event_type == "debate_turn":
                            _speaker = event.get("speaker") or _data.get("speaker")
                            if _speaker:
                                _light_event["agent_name"] = str(_speaker)
                            _speaker_role = event.get("speaker_role") or _data.get("speaker_role")
                            if _speaker_role:
                                _light_event["agent_role"] = str(_speaker_role)
                            _turn = event.get("turn") or _data.get("turn")
                            if _turn is not None:
                                _light_event["turn"] = _turn
                            _total = event.get("total_turns") or _data.get("total_turns")
                            if _total is not None:
                                _light_event["total_turns"] = _total
                        # Report building
                        if event_type == "report_building":
                            _section = event.get("section") or _data.get("section") or event.get("status") or _data.get("status") or ""
                            if _section:
                                _light_event["content"] = str(_section)[:500]
                        # Step title/icon
                        if event_type == "step":
                            _light_event["title"] = event.get("title") or _data.get("title", "")
                            _icon = event.get("icon") or _data.get("icon")
                            if _icon:
                                _light_event["icon"] = str(_icon)
                        # Round number for debates
                        if event_type in ("swarm_round", "debate_proposal", "debate_rebuttal",
                                          "debate_verdict", "debate_message", "debate_tool_call",
                                          "debate_tool_result", "debate_agent_plan", "debate_turn",
                                          "action_tool_call", "action_tool_result"):
                            _round = event.get("round") or _data.get("round") or event.get("turn") or _data.get("turn")
                            if _round is not None:
                                _light_event["round"] = _round
                        running.collected_events.append(_light_event)

                    # This is the ACTUAL time the event occurred (captured at source).
                    # Only fallback to now() if source didn't set it.
                    _event_timestamp = (
                        event.get("_source_timestamp", None)
                        or event.get("timestamp")
                        or datetime.now(timezone.utc).isoformat()
                    )

                    # Enrich event with sequence and identifiers
                    enriched_event = {
                        **event,
                        "sequence": running.last_event_seq,
                        "task_id": running.task_id,
                        "session_id": running.session_id,
                        "conversation_id": running.session_id,
                        "timestamp": _event_timestamp,
                    }

                    # Track current tool
                    if event_type == "tool_start":
                        running.current_tool = event.get("data", {}).get("tool", event.get("tool", "unknown"))
                    elif event_type in ("tool_done", "tool_error"):
                        running.current_tool = None

                    if event_type == "token":
                        running.tokens_streamed = True

                    # ALL OTHER persistable events → guaranteed async (NEVER drop)
                    _CRITICAL_PERSIST = {"task_completed", "task_failed", "task_cancelled"}

                    if event_type in _PERSIST_EVENT_TYPES or event_type in _CRITICAL_PERSIST:
                        _is_critical = event_type in _CRITICAL_PERSIST

                        if _is_critical:
                            # SYNC persist — await directly, NEVER lose these
                            await _persist_event_immediate(enriched_event)
                        else:
                            # ALL persistable events — guaranteed async (never drop).
                            # Semaphore inside _persist_event_immediate limits concurrent
                            # DB writes; events queue in memory until persisted.
                            asyncio.create_task(
                                _persist_event_immediate(enriched_event),
                                name=f"persist-{running.task_id[:8]}-{running.last_event_seq}",
                            )

                    # Push to WS subscribers (token streaming + live events)
                    await self._emit_event(running.session_id, enriched_event)

                    # Broadcast task_started to Redis for tab indicators
                    if event_type == "task_started":
                        await self._broadcast_execution_event(
                            event_type, enriched_event, running.session_id,
                            user_id=running.user_id,
                        )

                async def _heartbeat_loop() -> None:
                    """Periodically update last_heartbeat in DB for watchdog detection.

                    DEBATE-VIS-FIX: Also emits task_heartbeat event to SSE subscribers
                    so the SSE stream never goes idle during long LLM calls. This prevents
                    the stale detection in chat.py from killing the stream prematurely.

                    BUG-7 FIX: Also updates running.last_activity so SSE stale detection
                    in chat.py sees fresh activity even during long LLM calls. Previously,
                    only _event_bridge updated last_activity — heartbeat bypassed it,
                    causing false stale detection when LLM calls took >300s.
                    """
                    from sqlalchemy import update as _upd
                    while True:
                        await asyncio.sleep(10)
                        try:
                            # SSE stale detection from killing live streams.
                            running.last_activity = datetime.now(timezone.utc)

                            async with _session_factory() as hb_db:
                                await hb_db.execute(
                                    _upd(TaskModel)
                                    .where(TaskModel.id == uuid.UUID(running.task_id))
                                    .values(
                                        last_heartbeat=datetime.now(timezone.utc),
                                        heartbeat_count=TaskModel.heartbeat_count + 1,
                                    )
                                )
                                await hb_db.commit()
                            # This keeps the SSE stream alive by providing periodic events
                            # even during long LLM calls (30-120s between real events).
                            # The event is NOT persisted to DB (not in _PERSIST_EVENT_TYPES).
                            await self._emit_event(running.session_id, {
                                "event_type": "task_heartbeat",
                                "task_id": running.task_id,
                                "session_id": running.session_id,
                                "last_activity_desc": running.last_activity_desc,
                                "current_tool": running.current_tool,
                                "sequence": running.last_event_seq,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            })
                        except asyncio.CancelledError:
                            break
                        except Exception as hb_exc:
                            # Best-effort — don't crash the task for a heartbeat
                            # failure. But DO log it: a dropped heartbeat write
                            # (DB pool exhaustion / event-loop starvation) is the
                            # exact condition that previously made the watchdog
                            # mark a live task "crashed". Logging makes the
                            # false-positive root cause visible instead of silent.
                            logger.warning(
                                "heartbeat_write_failed",
                                task_id=running.task_id,
                                session_id=running.session_id,
                                error=str(hb_exc),
                            )

                _heartbeat_task = asyncio.create_task(
                    _heartbeat_loop(), name=f"heartbeat-{running.task_id[:8]}"
                )

                # Run the orchestrator
                try:
                    result = await orchestrator.process_user_request(
                        message=running.message,
                        user_id=running.user_id,
                        conversation_id=running.session_id,
                        event_callback=_event_bridge,
                        use_free=running.use_free,
                        resume_checkpoint=resume_checkpoint,
                    )
                finally:
                    # Always stop heartbeat when orchestrator finishes
                    _heartbeat_task.cancel()
                    try:
                        await _heartbeat_task
                    except (asyncio.CancelledError, Exception):
                        pass

                # Store result but keep status="running" until AFTER message persist.
                # This prevents frontend from seeing "completed" before assistant
                # message is in conversation_messages table.
                running.result = result

                try:
                    await audit.log_agent_end("orchestrator", success=True, duration_ms=0)
                    await audit.log_phase("task_execution", "complete")
                    await audit.log_summary()
                    remove_audit_logger(running.session_id)
                except Exception:
                    pass

                # under sustained load with many completed tasks.
                try:
                    from app.core.cancellation import unregister_token
                    unregister_token(running.task_id)
                except Exception:
                    pass

                # Persist assistant message FIRST, then mark completed.
                # Previously status was set to "completed" before persist,
                # causing frontend to see task done before message existed in DB.
                final_message = result.get("message", "")
                if not final_message:
                    final_message = "Task completed but no response generated."
                await self._persist_assistant_message(
                    running, final_message, result
                )

                # NOW mark completed in-memory (after message is persisted)
                running.status = "completed"

                # CRIT-02 FIX: Release collected_events memory after successful persist.
                # Without this, completed tasks hold up to 5000 events × 200 tasks = ~2GB.
                running.collected_events = []

                # P1-05 FIX: Adaptive drain timeout — debate tasks (high event count)
                # need longer drain to persist final synthesis/report events.
                # Standard tasks: 5s. Debate tasks (>200 events): 10s.
                _is_debate_task = running.last_event_seq > 200
                _drain_iterations = 100 if _is_debate_task else 50  # 10s or 5s
                _drain_timeout = 10.0  # seconds — absolute timeout guard
                _drain_start = time.monotonic()
                if self._pending_persists > 0:
                    for _ in range(_drain_iterations):
                        if self._pending_persists <= 0:
                            break
                        if (time.monotonic() - _drain_start) > _drain_timeout:
                            logger.warning("persist_drain_timeout", pending=self._pending_persists)
                            self._pending_persists = 0  # Force reset
                            break
                        await asyncio.sleep(0.1)

                # Also store result_content for F5-recovery fallback (GET /tasks/{id}).
                # P0-02 FIX: Increased from 2KB → 50KB. When persist_assistant_message
                # fails (DB pool exhausted, timeout), frontend falls back to task.result.content.
                # 2KB was too short — users saw truncated garbage. 50KB covers 95% of responses.
                await self._update_task_db_status(
                    running.task_id, "completed",
                    result_content=final_message[:50000],
                    respect_cancel=True,
                )

                # Emit completion event — persist-before-broadcast (no silent death).
                await self._finalize_terminal(running, "task_completed", {
                    "message": final_message,
                    "status": result.get("status"),
                    "agents_spawned": result.get("agents_spawned", []),
                    "swarm_id": result.get("swarm_id"),
                    "metadata": result.get("metadata"),
                    "summary": final_message[:500] if final_message else "",
                    "total_events": running.last_event_seq + 1,
                    "duration_ms": int(
                        (datetime.now(timezone.utc) - running.started_at).total_seconds() * 1000
                    ),
                })

                logger.info("background_task_completed",
                            task_id=running.task_id,
                            session_id=running.session_id)

        except asyncio.CancelledError:
            running.status = "cancelled"
            logger.info("background_task_cancelled", task_id=running.task_id)
            await self._update_task_db_status(running.task_id, "cancelled")

            # When CancelledError comes from external sources (shutdown, manual asyncio
            # cancel), cancel_task() never ran → subscribers never receive terminal event
            # → SSE hangs until 300s stale timeout. This safety net ensures delivery.
            # _finalize_terminal is idempotent, so it no-ops if cancel_task already ran.
            await self._finalize_terminal(
                running, "task_cancelled", {"reason": "cancelled_external"}
            )

            try:
                from app.core.cancellation import unregister_token
                unregister_token(running.task_id)
            except Exception:
                pass

        except Exception as e:
            _is_oom = isinstance(e, MemoryError)
            _status = "crashed" if _is_oom else "failed"
            running.status = _status
            running.error = str(e)
            logger.error("background_task_failed",
                         error=str(e),
                         task_id=running.task_id,
                         session_id=running.session_id,
                         is_oom=_is_oom)

            try:
                await audit.log_error(e, context="task_execution")
                await audit.log_agent_end("orchestrator", success=False, duration_ms=0)
                await audit.log_phase("task_execution", "complete")
                await audit.log_summary()
                remove_audit_logger(running.session_id)
            except Exception:
                pass
            await self._update_task_db_status(running.task_id, _status, error=str(e))
            try:
                from app.core.cancellation import unregister_token
                unregister_token(running.task_id)
            except Exception:
                pass

            # Emit failure event (with resumable hint for frontend)
            await self._finalize_terminal(running, "task_failed", {
                "error": str(e),
                "is_oom": _is_oom,
                "resumable": True,
            })

        finally:
            # BUG-12 FIX: Clear events to prevent memory leak in completed cache.
            # Previously only cleared in success path — failed/cancelled tasks
            # retained up to 5000 events × 200 cache slots = potential ~2GB leak.
            running.collected_events = []

            # Move to completed tasks cache
            if running.session_id in self._completed_tasks:
                self._completed_tasks.move_to_end(running.session_id)
            self._completed_tasks[running.session_id] = running
            while len(self._completed_tasks) > self.MAX_COMPLETED_CACHE:
                self._completed_tasks.popitem(last=False)  # Remove oldest

            # Clean up from running dict after delay
            # BUG-2 FIX: Reduced from 5s to 2s. Status is already terminal
            # (set in success/failure/cancel paths above), so new requests
            # via start_task() won't attempt to cancel this task.
            await asyncio.sleep(2)
            async with self._lock:
                if self._running_tasks.get(running.session_id) is running:
                    del self._running_tasks[running.session_id]

    async def _finalize_terminal(
        self, running: RunningTask, event_type: str, data: dict[str, Any]
    ) -> None:
        """Emit a terminal event, guaranteeing DB persistence before broadcast.

        Frontend /chat polls the DB for a terminal event (task_completed /
        task_failed / task_cancelled) to know a task is done. The DB write is
        therefore the load-bearing step: if it is skipped (or its error is
        silently swallowed), the row stays `executing` forever and the frontend
        polls indefinitely ("silent death").

        Guarantees:
          - Idempotent via running._terminal_emitted (safe to call twice).
          - PERSIST FIRST — mandatory, retried once; on final failure the error
            is logged with exc_info and NOT swallowed silently.
          - BROADCAST AFTER — _emit_event (live subscribers) + Redis broadcast
            are best-effort; their failures are logged at warning and never
            prevent the persisted terminal state.

        Never raises into the caller: the task's finally-block cleanup must run.
        """
        if running._terminal_emitted:
            return
        running._terminal_emitted = True

        running.last_event_seq += 1
        event = {
            "event_type": event_type,
            "task_id": running.task_id,
            "session_id": running.session_id,
            "sequence": running.last_event_seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        # 1. PERSIST FIRST (mandatory) — retry once, never swallow on failure.
        for attempt in range(2):
            try:
                await self._persist_event(running, event)
                break
            except Exception:
                if attempt == 0:
                    continue
                logger.error(
                    "terminal_event_persist_failed",
                    task_id=running.task_id,
                    session_id=running.session_id,
                    event_type=event_type,
                    exc_info=True,
                )

        # 2. BROADCAST AFTER (best-effort) — live subscribers + cross-instance.
        try:
            await self._emit_event(running.session_id, event)
        except Exception as exc:
            logger.warning(
                "terminal_event_emit_failed",
                error=str(exc), task_id=running.task_id, event_type=event_type,
            )
        try:
            await self._broadcast_terminal_event(
                event_type, event, running.session_id, user_id=running.user_id,
            )
        except Exception as exc:
            logger.warning(
                "terminal_event_broadcast_failed",
                error=str(exc), task_id=running.task_id, event_type=event_type,
            )

    async def _persist_event(self, running: RunningTask, event: dict[str, Any]) -> None:
        """Persist a task event to the database for replay.

        AUDIT-FIX-2: Handles duplicate TaskModel creation race with _flush_event_buffer.
        Uses INSERT with conflict handling to avoid IntegrityError when both paths
        attempt to create the same TaskModel simultaneously.
        """
        from app.core.dependencies import _session_factory
        from app.models.database import TaskEventModel, TaskModel
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError

        if not _session_factory:
            return

        try:
            async with _session_factory() as db:
                # First try to find existing task (created by _flush_event_buffer or earlier persist)
                result = await db.execute(
                    select(TaskModel.id)
                    .where(TaskModel.id == uuid.UUID(running.task_id))
                    .limit(1)
                )
                db_task_id = result.scalar()

                if not db_task_id:
                    # Fallback: check by session_id (older tasks)
                    result2 = await db.execute(
                        select(TaskModel.id)
                        .where(TaskModel.session_id == running.session_id)
                        .order_by(TaskModel.created_at.desc())
                        .limit(1)
                    )
                    db_task_id = result2.scalar()

                if not db_task_id:
                    # where _flush_event_buffer created the task between our check and insert.
                    try:
                        new_task = TaskModel(
                            id=uuid.UUID(running.task_id),
                            title=running.message[:200] if running.message else "Background task",
                            status="executing",
                            user_id=running.user_id,
                            session_id=running.session_id,
                        )
                        db.add(new_task)
                        await db.flush()
                        db_task_id = new_task.id
                    except IntegrityError:
                        await db.rollback()
                        # Task was created by concurrent flush — use its ID
                        db_task_id = uuid.UUID(running.task_id)

                _event_data = event.get("data") if "data" in event else {
                    k: v for k, v in event.items()
                    if k not in ("event_type", "sequence", "task_id", "session_id")
                }
                if "timestamp" not in _event_data and "timestamp" in event:
                    _event_data["timestamp"] = event["timestamp"]
                db.add(TaskEventModel(
                    task_id=db_task_id,
                    session_id=running.session_id,
                    event_type=event.get("event_type", "unknown"),
                    data=_event_data,
                    sequence=event.get("sequence", running.last_event_seq),
                ))
                await db.commit()
        except Exception as exc:
            logger.debug("event_db_persist_error", error=str(exc))

    async def _broadcast_terminal_event(
        self, event_type: str, event: dict[str, Any], session_id: str,
        *, user_id: str = "",
    ) -> None:
        """Broadcast terminal events (completed/failed/cancelled) to Redis.

        Published to Monitor WebSocket for multi-tab delivery.

        BUG-2+3 FIX: Added user_id parameter. Previously, terminal events
        lacked user_id in payload, causing Monitor WS AUDIT-M3 filter to
        pass them through to ALL connected users (privacy leak).
        """
        try:
            from app.communication.message_bus import get_message_bus
            bus = await get_message_bus()
            event_data = event.get("data", event)
            if not isinstance(event_data, dict):
                event_data = event
            enrichments: dict[str, Any] = {}
            if "task_id" not in event_data and "task_id" in event:
                enrichments["task_id"] = event["task_id"]
            if "session_id" not in event_data:
                enrichments["session_id"] = session_id
            if "conversation_id" not in event_data:
                enrichments["conversation_id"] = session_id
            if user_id and "user_id" not in event_data:
                enrichments["user_id"] = user_id
            if enrichments:
                event_data = {**event_data, **enrichments}
            await bus.emit_event_scoped(
                event_type, event_data, session_id=session_id
            )
        except Exception as exc:
            logger.debug("terminal_event_broadcast_failed",
                         error=str(exc), event_type=event_type)

    async def _broadcast_execution_event(
        self, event_type: str, event: dict[str, Any], session_id: str,
        *, user_id: str = "",
    ) -> None:
        """Broadcast non-terminal execution events to Redis for multi-tab delivery.

        BUG-MULTI-TAB FIX: Previously, only terminal events (completed/failed)
        were broadcast. This meant other tabs/users viewing the same session
        via Monitor WS never received real-time execution progress (tool calls,
        agent spawns, debate events, etc.).

        This method broadcasts important execution events so:
        - Other browser tabs receive events via Monitor WS (useWebSocket hook)
        - Other users viewing the same session see real-time progress
        - Dashboard/admin views show live execution state

        High-frequency events (token, agent_thinking) are NOT broadcast here
        to avoid flooding the message bus. They're only delivered via direct
        Chat WS subscription.

        BUG-2+3 FIX: Added user_id parameter for Monitor WS AUDIT-M3 filtering.
        """
        try:
            from app.communication.message_bus import get_message_bus
            bus = await get_message_bus()

            # Build flat event payload for Monitor WS consumption
            # Include session_id/conversation_id so frontend can route correctly
            event_data = {k: v for k, v in event.items()
                         if k not in ("event_type",)}
            if "session_id" not in event_data:
                event_data["session_id"] = session_id
            if "conversation_id" not in event_data:
                event_data["conversation_id"] = session_id
            if user_id and "user_id" not in event_data:
                event_data["user_id"] = user_id

            await bus.emit_event_scoped(
                event_type, event_data, session_id=session_id
            )
        except Exception as exc:
            # Best-effort — never crash the task for broadcast failures
            logger.debug("execution_event_broadcast_failed",
                         error=str(exc), event_type=event_type)

    async def _persist_assistant_message(
        self,
        running: RunningTask,
        content: str,
        orchestrator_result: dict[str, Any],
    ) -> None:
        """Persist assistant response with metadata to ConversationMessageModel.

        This is the SINGLE persist path for assistant messages.
        Both SSE and WS paths go through here (no dual-persist race).
        Includes execution_events metadata for history replay.

        RELIABILITY FIX: Retries up to 3 times on transient DB errors.
        This is the MOST CRITICAL persist operation — if it fails, the user's
        response is permanently lost from conversation history. Frontend shows
        "Phản hồi trước đó không được lưu lại" error on next page load.
        """
        from app.core.dependencies import _session_factory
        from app.models.database import ConversationMessageModel

        if not _session_factory:
            return

        _max_retries = 3
        _last_error: Optional[Exception] = None

        for _attempt in range(_max_retries):
            try:
                async with _session_factory() as db:
                    # Dedup: skip if identical message exists within 60s
                    from sqlalchemy import select as _sel, func as _fn
                    from datetime import timedelta
                    _cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
                    _dup_result = await db.execute(
                        _sel(_fn.count()).select_from(ConversationMessageModel).where(
                            ConversationMessageModel.session_id == running.session_id,
                            ConversationMessageModel.role == "assistant",
                            ConversationMessageModel.content == content,
                            ConversationMessageModel.created_at >= _cutoff,
                        )
                    )
                    if (_dup_result.scalar() or 0) > 0:
                        logger.debug("assistant_persist_skip_dup", session_id=running.session_id)
                        return

                    # Build metadata from orchestrator result + collected events
                    msg_metadata = self._build_message_metadata(running, orchestrator_result)

                    # Content size guard (500KB cap)
                    _MAX_CONTENT_BYTES = 500_000
                    _final_content = content
                    if len(_final_content) > _MAX_CONTENT_BYTES:
                        _final_content = _final_content[:_MAX_CONTENT_BYTES] + "\n\n…[content truncated for storage]"

                    msg = ConversationMessageModel(
                        session_id=running.session_id,
                        role="assistant",
                        content=_final_content,
                    )
                    if msg_metadata:
                        msg.metadata_ = msg_metadata
                    db.add(msg)
                    await db.commit()

                    logger.info("assistant_message_persisted",
                                session_id=running.session_id,
                                content_len=len(_final_content))
                    return  # Success — exit retry loop
            except asyncio.CancelledError:
                raise  # Never suppress cancellation
            except Exception as exc:
                _last_error = exc
                if _attempt < _max_retries - 1:
                    await asyncio.sleep(0.5 * (_attempt + 1))  # Backoff: 0.5s, 1.0s
                    logger.debug("assistant_persist_retry",
                                 attempt=_attempt + 1,
                                 error=str(exc),
                                 session_id=running.session_id)

        # All retries exhausted
        logger.warning("assistant_message_persist_failed",
                       error=str(_last_error),
                       session_id=running.session_id,
                       attempts=_max_retries)

    def _build_message_metadata(
        self,
        running: RunningTask,
        orchestrator_result: dict[str, Any],
    ) -> dict[str, Any]:
        """Build metadata dict for assistant message persistence.

        Includes task_id, strategy, model, agents_spawned, and execution_events.
        """
        msg_metadata: dict[str, Any] = {}

        if orchestrator_result.get("task_id"):
            msg_metadata["task_id"] = orchestrator_result["task_id"]
        if orchestrator_result.get("agents_spawned"):
            msg_metadata["agents_spawned"] = orchestrator_result["agents_spawned"]
        if orchestrator_result.get("status"):
            msg_metadata["status"] = orchestrator_result["status"]
        if orchestrator_result.get("swarm_id"):
            msg_metadata["swarm_id"] = orchestrator_result["swarm_id"]

        orch_meta = orchestrator_result.get("metadata") or {}
        if orch_meta.get("strategy"):
            msg_metadata["strategy"] = str(orch_meta["strategy"])
        if orch_meta.get("model"):
            msg_metadata["model"] = str(orch_meta["model"])
        if orch_meta.get("rounds"):
            msg_metadata["rounds"] = int(orch_meta["rounds"])
        if orch_meta.get("xskill"):
            msg_metadata["xskill"] = orch_meta["xskill"]

        # Persist execution events (truncated for size)
        _MAX_METADATA_BYTES = 800_000
        if running.collected_events:
            truncated_events: list[dict[str, Any]] = []
            _running_size = 0
            for evt in running.collected_events:
                tevt = dict(evt)
                # Truncate large fields
                for fld in ("output", "content", "preview", "output_preview"):
                    val = tevt.get(fld)
                    if isinstance(val, str) and len(val) > 8000:
                        tevt[fld] = val[:8000] + "…[truncated]"
                params_val = tevt.get("params")
                if isinstance(params_val, str) and len(params_val) > 4000:
                    tevt["params"] = params_val[:4000] + "…[truncated]"

                try:
                    _evt_size = len(json.dumps(tevt, default=str, ensure_ascii=False))
                except Exception:
                    _evt_size = 1000  # Estimate on serialization failure
                if _running_size + _evt_size > _MAX_METADATA_BYTES:
                    truncated_events.append({
                        "type": "truncation_notice",
                        "message": f"Events truncated: {len(running.collected_events) - len(truncated_events)} remaining omitted",
                    })
                    break
                _running_size += _evt_size
                truncated_events.append(tevt)

            msg_metadata["execution_events"] = truncated_events
            if len(truncated_events) < len(running.collected_events):
                msg_metadata["events_truncated"] = True
                msg_metadata["total_event_count"] = len(running.collected_events)

        return msg_metadata


    async def drain_pending_persists(self, timeout: float = 10.0) -> int:
        """Wait for pending event persist tasks to complete.

        Called during graceful shutdown to prevent data loss.
        Returns number of persists that were still pending.

        HIGH-04 FIX: Increased default timeout from 5s → 10s.
        Under sustained load (swarm debates: 20-50 events/sec), 5s was
        insufficient to drain all pending fire-and-forget persist tasks,
        causing event loss on graceful shutdown.

        Args:
            timeout: Maximum seconds to wait for pending persists.
        """
        if self._pending_persists <= 0:
            return 0

        initial_pending = self._pending_persists
        logger.info("draining_pending_persists", pending=initial_pending)

        # Poll until all persists complete or timeout
        elapsed = 0.0
        poll_interval = 0.1
        while self._pending_persists > 0 and elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        remaining = self._pending_persists
        if remaining > 0:
            logger.warning("drain_timeout_persists_remaining",
                           remaining=remaining, timeout=timeout)
        else:
            logger.info("drain_complete", initial=initial_pending, elapsed_s=round(elapsed, 2))
        return initial_pending

    async def cleanup_stale_tasks(self, max_idle_seconds: float = 600.0) -> int:
        """Remove tasks stuck in 'running' state without recent activity.

        A task is considered stale if:
        1. Status is 'running'
        2. last_activity is older than max_idle_seconds
        3. asyncio_task is done (finished without cleanup) OR asyncio_task is None

        This handles the edge case where an exception occurs BEFORE the asyncio_task
        is properly set, leaving the task in _running_tasks forever.

        Returns number of stale tasks cleaned up.
        """
        now = datetime.now(timezone.utc)
        stale_sessions: list[str] = []

        async with self._lock:
            for session_id, task in list(self._running_tasks.items()):
                if task.status != "running":
                    continue

                idle_seconds = (now - task.last_activity).total_seconds()
                if idle_seconds < max_idle_seconds:
                    continue

                # Check if asyncio task is actually dead
                is_dead = (
                    task.asyncio_task is None
                    or task.asyncio_task.done()
                )

                if is_dead:
                    stale_sessions.append(session_id)

            # Clean up stale tasks (still within lock to safely modify dict)
            cleaned = 0
            for session_id in stale_sessions:
                task = self._running_tasks.get(session_id)
                if task:
                    logger.warning(
                        "stale_task_cleanup",
                        session_id=session_id,
                        task_id=task.task_id,
                        idle_seconds=round((now - task.last_activity).total_seconds()),
                        asyncio_task_done=task.asyncio_task.done() if task.asyncio_task else True,
                    )
                    task.status = "failed"
                    task.error = "Task became stale (no activity, asyncio task dead)"
                    # Move to completed cache
                    self._completed_tasks[session_id] = task
                    while len(self._completed_tasks) > self.MAX_COMPLETED_CACHE:
                        self._completed_tasks.popitem(last=False)
                    # Remove from running
                    del self._running_tasks[session_id]
                    cleaned += 1

        # DB updates and event emissions outside lock to avoid deadlock
        for session_id in stale_sessions:
            task = self._completed_tasks.get(session_id)
            if task:
                await self._update_task_db_status(
                    task.task_id, "failed",
                    error="Task stale: no activity and background task terminated"
                )
                await self._emit_event(session_id, {
                    "event_type": "task_failed",
                    "task_id": task.task_id,
                    "session_id": session_id,
                    "sequence": task.last_event_seq + 1,
                    "timestamp": now.isoformat(),
                    "data": {"error": "Task became stale and was cleaned up"},
                })

        if cleaned:
            logger.info("stale_tasks_cleaned", count=cleaned)
        return cleaned


# ─── Singleton ───────────────────────────────────────────────

_task_runner: Optional[TaskRunner] = None


def get_task_runner() -> TaskRunner:
    """Get the singleton TaskRunner instance."""
    global _task_runner
    if _task_runner is None:
        _task_runner = TaskRunner()
    return _task_runner
