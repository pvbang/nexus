"""
Nexus - Background Task Tracker & Notifications.

Tracks long-running background tasks and notifies users across all connected
tabs when tasks complete. Provides a task status panel for the frontend.

Hermes-agent pattern: Tasks survive WebSocket disconnects and notify on completion.

Features:
- Track all tasks per user: pending/executing/completed/failed
- Notify all user's tabs when a background task completes
- Task result storage for later retrieval
- Periodic cleanup of old completed tasks
- Integration with WebSocket ConnectionManager for multi-tab delivery
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)

# Configuration
MAX_COMPLETED_TASKS = 100  # Keep last N completed tasks per user
COMPLETED_TASK_TTL = 3600.0  # Remove completed tasks after 1 hour
CLEANUP_INTERVAL = 300.0  # Run cleanup every 5 minutes


class TaskStatus(str, Enum):
    """Status of a background task."""

    pending = "pending"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class BackgroundTask:
    """Represents a tracked background task."""

    task_id: str
    user_id: str
    session_id: str
    description: str
    status: TaskStatus = TaskStatus.pending
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None
    progress: float = 0.0  # 0.0 to 1.0
    progress_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def elapsed_seconds(self) -> float:
        """Time since task was created."""
        end = self.completed_at or time.time()
        start = self.started_at or self.created_at
        return end - start

    @property
    def is_terminal(self) -> bool:
        """Whether the task is in a terminal state."""
        return self.status in (
            TaskStatus.completed,
            TaskStatus.failed,
            TaskStatus.cancelled,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for API/WebSocket delivery."""
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "description": self.description,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result_preview": (self.result or "")[:500] if self.result else None,
            "error": self.error,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "metadata": self.metadata,
        }


class BackgroundTaskTracker:
    """
    Central tracker for all background tasks across users.

    Provides:
    - Task registration and status updates
    - Per-user task listing (for status panel)
    - Notification delivery to all user's tabs via callbacks
    - Periodic cleanup of old completed tasks

    Usage:
        tracker = get_background_task_tracker()

        # Register a new task
        task = tracker.create_task(
            user_id="user1",
            session_id="conv123",
            description="Analyzing document...",
        )

        # Update progress
        tracker.update_progress(task.task_id, 0.5, "Halfway done...")

        # Mark complete (triggers notification)
        await tracker.complete_task(task.task_id, result="Analysis complete: ...")

        # Get all tasks for user (for status panel)
        tasks = tracker.get_user_tasks("user1")
    """

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}  # task_id → task
        self._user_tasks: dict[str, list[str]] = {}  # user_id → [task_ids]
        self._notification_callbacks: dict[str, list[Callable]] = {}  # user_id → callbacks
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    def create_task(
        self,
        user_id: str,
        session_id: str,
        description: str,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> BackgroundTask:
        """
        Register a new background task.

        Args:
            user_id: Owner of the task.
            session_id: Related conversation session.
            description: Human-readable task description.
            metadata: Optional extra metadata.

        Returns:
            The created BackgroundTask instance.
        """
        task_id = str(uuid.uuid4())[:12]
        task = BackgroundTask(
            task_id=task_id,
            user_id=user_id,
            session_id=session_id,
            description=description,
            metadata=metadata or {},
        )

        self._tasks[task_id] = task

        if user_id not in self._user_tasks:
            self._user_tasks[user_id] = []
        self._user_tasks[user_id].append(task_id)

        logger.info(
            "background_task_created",
            task_id=task_id,
            user_id=user_id,
            session_id=session_id,
            description=description[:100],
        )

        return task

    def start_task(self, task_id: str) -> bool:
        """Mark task as executing."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        task.status = TaskStatus.executing
        task.started_at = time.time()
        return True

    def update_progress(
        self,
        task_id: str,
        progress: float,
        message: str = "",
    ) -> bool:
        """
        Update task progress.

        Args:
            task_id: Task to update.
            progress: Progress value (0.0 to 1.0).
            message: Optional progress description.
        """
        task = self._tasks.get(task_id)
        if task is None or task.is_terminal:
            return False
        task.progress = min(max(progress, 0.0), 1.0)
        task.progress_message = message
        return True

    async def complete_task(
        self,
        task_id: str,
        result: str = "",
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Mark task as completed and notify user.

        Sends notification to all user's connected tabs.

        Args:
            task_id: Task to complete.
            result: Task result/output.
            metadata: Optional result metadata.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return False

        task.status = TaskStatus.completed
        task.completed_at = time.time()
        task.result = result
        task.progress = 1.0
        if metadata:
            task.metadata.update(metadata)

        logger.info(
            "background_task_completed",
            task_id=task_id,
            user_id=task.user_id,
            elapsed_seconds=round(task.elapsed_seconds, 1),
        )

        # Notify user across all tabs
        await self._notify_user(task.user_id, {
            "type": "task_notification",
            "event": "completed",
            "task": task.to_dict(),
        })

        return True

    async def fail_task(
        self,
        task_id: str,
        error: str = "",
    ) -> bool:
        """Mark task as failed and notify user."""
        task = self._tasks.get(task_id)
        if task is None:
            return False

        task.status = TaskStatus.failed
        task.completed_at = time.time()
        task.error = error

        logger.warning(
            "background_task_failed",
            task_id=task_id,
            user_id=task.user_id,
            error=error[:200],
        )

        await self._notify_user(task.user_id, {
            "type": "task_notification",
            "event": "failed",
            "task": task.to_dict(),
        })

        return True

    async def cancel_task(self, task_id: str) -> bool:
        """Mark task as cancelled."""
        task = self._tasks.get(task_id)
        if task is None or task.is_terminal:
            return False

        task.status = TaskStatus.cancelled
        task.completed_at = time.time()

        await self._notify_user(task.user_id, {
            "type": "task_notification",
            "event": "cancelled",
            "task": task.to_dict(),
        })

        return True

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        """Get a specific task by ID."""
        return self._tasks.get(task_id)

    def get_user_tasks(
        self,
        user_id: str,
        *,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> list[BackgroundTask]:
        """
        Get all tasks for a user (for status panel).

        Args:
            user_id: User to query.
            include_terminal: Include completed/failed tasks.
            limit: Max tasks to return.

        Returns:
            List of BackgroundTask sorted by creation time (newest first).
        """
        task_ids = self._user_tasks.get(user_id, [])
        tasks: list[BackgroundTask] = []

        for tid in reversed(task_ids):  # newest first
            task = self._tasks.get(tid)
            if task is None:
                continue
            if not include_terminal and task.is_terminal:
                continue
            tasks.append(task)
            if len(tasks) >= limit:
                break

        return tasks

    def get_active_tasks(self, user_id: str) -> list[BackgroundTask]:
        """Get only pending/executing tasks for a user."""
        return self.get_user_tasks(user_id, include_terminal=False)

    # ── Notification subscription ──

    def subscribe_notifications(
        self,
        user_id: str,
        callback: Callable,
    ) -> Callable:
        """
        Subscribe to task notifications for a user.

        Returns an unsubscribe callable.
        """
        if user_id not in self._notification_callbacks:
            self._notification_callbacks[user_id] = []
        self._notification_callbacks[user_id].append(callback)

        def unsubscribe():
            cbs = self._notification_callbacks.get(user_id, [])
            self._notification_callbacks[user_id] = [
                cb for cb in cbs if cb != callback
            ]

        return unsubscribe

    async def _notify_user(self, user_id: str, event: dict[str, Any]) -> None:
        """Send notification to all user's subscribed callbacks."""
        callbacks = self._notification_callbacks.get(user_id, [])
        dead_callbacks: list[Callable] = []

        for cb in callbacks:
            try:
                await cb(event)
            except Exception:
                dead_callbacks.append(cb)

        # Clean up dead callbacks
        if dead_callbacks:
            self._notification_callbacks[user_id] = [
                cb for cb in callbacks if cb not in dead_callbacks
            ]

    # ── Cleanup ──

    async def cleanup_old_tasks(self) -> int:
        """Remove old completed/failed tasks. Returns count removed."""
        now = time.time()
        removed = 0

        async with self._lock:
            to_remove: list[str] = []

            for task_id, task in self._tasks.items():
                if task.is_terminal and task.completed_at:
                    if (now - task.completed_at) > COMPLETED_TASK_TTL:
                        to_remove.append(task_id)

            for task_id in to_remove:
                task = self._tasks.pop(task_id)
                # Remove from user index
                user_tasks = self._user_tasks.get(task.user_id, [])
                self._user_tasks[task.user_id] = [
                    t for t in user_tasks if t != task_id
                ]
                removed += 1

        if removed:
            logger.info("background_tasks_cleanup", removed=removed)

        return removed

    def get_stats(self) -> dict[str, Any]:
        """Get tracker statistics for monitoring."""
        status_counts: dict[str, int] = {}
        for task in self._tasks.values():
            status_counts[task.status.value] = (
                status_counts.get(task.status.value, 0) + 1
            )

        return {
            "total_tasks": len(self._tasks),
            "total_users": len(self._user_tasks),
            "notification_subscribers": sum(
                len(cbs) for cbs in self._notification_callbacks.values()
            ),
            "status_counts": status_counts,
        }

    async def start_cleanup_loop(self) -> None:
        """Start periodic cleanup in background."""
        if self._cleanup_task is not None:
            return

        async def _cleanup_loop():
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL)
                try:
                    await self.cleanup_old_tasks()
                except Exception as e:
                    logger.warning("background_task_cleanup_error", error=str(e))

        self._cleanup_task = asyncio.create_task(
            _cleanup_loop(), name="bg_task_cleanup"
        )


# ── Module-level singleton ──

_global_tracker: Optional[BackgroundTaskTracker] = None


def get_background_task_tracker() -> BackgroundTaskTracker:
    """Get or create the global background task tracker."""
    global _global_tracker
    if _global_tracker is None:
        _global_tracker = BackgroundTaskTracker()
    return _global_tracker
