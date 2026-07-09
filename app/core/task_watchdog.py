"""
MekongAI Nexus - Task Watchdog.

Background service that detects dead/stuck tasks via heartbeat monitoring.
Periodically scans DB for tasks in "executing" state with stale heartbeats
and marks them as "crashed", notifying the user.

Runs alongside the main application — no external process needed.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


def is_task_dead(
    *,
    last_event_at: Optional[datetime],
    cutoff: datetime,
) -> bool:
    """Decide whether a stale-heartbeat task is genuinely dead.

    Pure function (no DB / no IO) so it can be unit-tested. Called only for
    tasks already filtered to status="executing" with a heartbeat older than the
    cutoff. A stale heartbeat alone is NOT proof of death: the heartbeat loop is
    best-effort and can miss writes when the DB pool is exhausted or the event
    loop is starved by a CPU-bound tool. The orchestrator keeps emitting
    task_events the whole time, so a recent event is the stronger liveness
    signal. The task is dead only if it has emitted NO event since the cutoff
    (no events at all, or the latest one is older than the cutoff).
    """
    if last_event_at is None:
        return True
    if last_event_at.tzinfo is None:
        last_event_at = last_event_at.replace(tzinfo=timezone.utc)
    return last_event_at < cutoff


def build_stale_terminal_event(
    task_id: str, session_id: str, sequence: int, timeout_seconds: int
) -> dict:
    """Build the terminal "task_failed" event for a stale (crashed) task.

    Pure function (no DB / no IO) so it can be unit-tested. The watchdog maps
    the returned dict onto a TaskEventModel row. The frontend /chat polling
    treats "task_failed" as a terminal event and reads data.error, so writing
    this event is what unsticks a session whose task stopped heartbeating.
    """
    return {
        "event_type": "task_failed",
        "task_id": task_id,
        "session_id": session_id,
        "sequence": sequence,
        "data": {
            "error": (
                f"Task stopped responding (no heartbeat for {timeout_seconds}s)."
            ),
            "resumable": True,
            "reason": "stale_heartbeat",
        },
    }


class TaskWatchdog:
    """Periodic watchdog that detects tasks that stopped heartbeating.

    A task is considered dead if:
    - status = "executing"
    - last_heartbeat is not NULL
    - last_heartbeat < NOW() - HEARTBEAT_TIMEOUT

    Dead tasks are marked as "crashed" and a notification is emitted
    to the user via message bus.
    """

    # A task not heartbeating for 2 minutes is considered dead
    HEARTBEAT_TIMEOUT = timedelta(seconds=120)
    # Check every 60 seconds
    CHECK_INTERVAL = 60.0

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._total_crashed_detected = 0

    async def start(self) -> None:
        """Start the watchdog background loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._watch_loop(), name="task-watchdog")
        logger.info(
            "task_watchdog_started",
            heartbeat_timeout_s=self.HEARTBEAT_TIMEOUT.total_seconds(),
            check_interval_s=self.CHECK_INTERVAL,
        )

    async def stop(self) -> None:
        """Stop the watchdog."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("task_watchdog_stopped", total_crashed=self._total_crashed_detected)

    async def _watch_loop(self) -> None:
        """Internal loop that periodically checks for dead tasks."""
        try:
            while self._running:
                await asyncio.sleep(self.CHECK_INTERVAL)
                try:
                    await self._check_dead_tasks()
                except Exception as e:
                    logger.warning("watchdog_check_error", error=str(e))
        except asyncio.CancelledError:
            pass

    async def _check_dead_tasks(self) -> None:
        """Find tasks with stale heartbeats and mark as crashed."""
        from app.core.dependencies import _session_factory
        from app.models.database import TaskEventModel, TaskModel
        from sqlalchemy import func, select

        if not _session_factory:
            return

        cutoff = datetime.now(timezone.utc) - self.HEARTBEAT_TIMEOUT

        try:
            async with _session_factory() as db:
                # Find executing tasks with stale heartbeat
                result = await db.execute(
                    select(TaskModel).where(
                        TaskModel.status == "executing",
                        TaskModel.last_heartbeat.isnot(None),
                        TaskModel.last_heartbeat < cutoff,
                    )
                )
                candidates = result.scalars().all()

                if not candidates:
                    return

                # A stale heartbeat alone does NOT mean the task is dead. The
                # heartbeat loop is best-effort and can miss writes when the DB
                # pool is exhausted or the event loop is starved by a CPU-bound
                # tool — yet the orchestrator keeps emitting task_events the whole
                # time. Those events are the STRONGEST liveness signal we have, so
                # before declaring a task crashed we confirm it has emitted no
                # event since the cutoff. A task with a fresh event is alive; only
                # tasks silent on BOTH heartbeat and events are genuinely dead.
                dead_tasks = []
                for task in candidates:
                    last_event_result = await db.execute(
                        select(func.max(TaskEventModel.created_at)).where(
                            TaskEventModel.task_id == task.id
                        )
                    )
                    latest_event = last_event_result.scalar()
                    if is_task_dead(last_event_at=latest_event, cutoff=cutoff):
                        dead_tasks.append(task)
                    else:
                        logger.info(
                            "watchdog_skip_alive_task",
                            task_id=str(task.id),
                            session_id=task.session_id,
                            last_heartbeat=str(task.last_heartbeat),
                            last_event_at=str(latest_event),
                            reason="stale heartbeat but recent task_event — task is alive",
                        )

                if not dead_tasks:
                    return

                timeout_s = int(self.HEARTBEAT_TIMEOUT.total_seconds())

                # Mark each as crashed
                for task in dead_tasks:
                    task.status = "crashed"
                    task.error = (
                        f"Task stopped responding (no heartbeat for "
                        f"{timeout_s}s). "
                        f"Last heartbeat: {task.last_heartbeat.isoformat() if task.last_heartbeat else 'unknown'}"
                    )
                    self._total_crashed_detected += 1
                    logger.warning(
                        "dead_task_detected",
                        task_id=str(task.id),
                        session_id=task.session_id,
                        last_heartbeat=str(task.last_heartbeat),
                    )

                    # Persist a terminal "task_failed" event so the frontend
                    # /chat polling (which reads task_events) stops waiting.
                    # Sequence = max existing sequence + 1, so the terminal
                    # event lands after every prior event for deterministic replay.
                    max_seq = await db.execute(
                        select(func.max(TaskEventModel.sequence)).where(
                            TaskEventModel.task_id == task.id
                        )
                    )
                    next_seq = (max_seq.scalar() or 0) + 1
                    evt = build_stale_terminal_event(
                        task_id=str(task.id),
                        session_id=task.session_id or "",
                        sequence=next_seq,
                        timeout_seconds=timeout_s,
                    )
                    db.add(TaskEventModel(
                        task_id=task.id,
                        session_id=task.session_id,
                        event_type=evt["event_type"],
                        data=evt["data"],
                        sequence=evt["sequence"],
                    ))

                await db.commit()
                logger.info(
                    "dead_tasks_marked_crashed",
                    count=len(dead_tasks),
                    total_crashed=self._total_crashed_detected,
                )

                # Notify users about crashed tasks
                await self._notify_crashed_tasks(dead_tasks)

        except Exception as e:
            logger.error("watchdog_db_error", error=str(e))

    async def _notify_crashed_tasks(self, tasks) -> None:
        """Emit crash notifications to affected sessions via message bus."""
        try:
            from app.communication.message_bus import get_message_bus

            bus = await get_message_bus()
            for task in tasks:
                if not task.session_id:
                    continue
                await bus.emit_event_scoped(
                    "task_crashed",
                    {
                        "task_id": str(task.id),
                        "session_id": task.session_id,
                        "user_id": task.user_id,
                        "title": task.title[:100] if task.title else "Unknown task",
                        "message": (
                            "Task stopped unexpectedly (process crashed or network issue). "
                            "You can retry by sending the same message."
                        ),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    session_id=task.session_id,
                )
        except Exception as exc:
            logger.debug("crash_notification_failed", error=str(exc))

    def get_status(self) -> dict:
        """Get watchdog status for health endpoint."""
        return {
            "running": self._running,
            "total_crashed_detected": self._total_crashed_detected,
            "heartbeat_timeout_s": self.HEARTBEAT_TIMEOUT.total_seconds(),
            "check_interval_s": self.CHECK_INTERVAL,
        }


# ─── Singleton ───────────────────────────────────────────────

_watchdog: Optional[TaskWatchdog] = None


def get_task_watchdog() -> TaskWatchdog:
    """Get the singleton TaskWatchdog instance."""
    global _watchdog
    if _watchdog is None:
        _watchdog = TaskWatchdog()
    return _watchdog
