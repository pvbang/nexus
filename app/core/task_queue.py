"""
Nexus - Task Queue for Heavy Requests.

Redis-based async task queue that prevents server overload
when many users submit complex orchestrator requests simultaneously.

Instead of running unlimited swarms in parallel, requests are queued
and processed with controlled concurrency.

Architecture:
    User Request → Enqueue → Worker picks up → Orchestrator runs → Result stored
    
    Max concurrent orchestrator tasks: configurable (default: 3)
    Queue overflow: returns 503 with estimated wait time
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class QueuedTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class QueuedTask:
    """A task waiting to be processed by the orchestrator."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    message: str = ""
    user_id: str = "default_user"
    conversation_id: Optional[str] = None
    status: QueuedTaskStatus = QueuedTaskStatus.QUEUED
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    position: int = 0  # queue position (0 = running)


class OrchestratorQueue:
    """
    Bounded async queue for orchestrator requests.

    Controls concurrency: max N orchestrator tasks run simultaneously.
    Extra requests wait in queue (FIFO). Prevents server overload.
    """

    def __init__(
        self,
        max_concurrent: int = 3,
        max_queue_size: int = 50,
    ):
        self.max_concurrent = max_concurrent
        self.max_queue_size = max_queue_size
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.Queue[QueuedTask] = asyncio.Queue(maxsize=max_queue_size)
        self._tasks: dict[str, QueuedTask] = {}
        self._active_count = 0
        self._total_processed = 0
        self._workers: list[asyncio.Task] = []
        self._running = False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def total_processed(self) -> int:
        return self._total_processed

    def start_workers(self, num_workers: int = 3) -> None:
        """Start background worker tasks."""
        if self._running:
            return
        self._running = True
        for i in range(num_workers):
            task = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(task)
        logger.info("orchestrator_queue_started", workers=num_workers, max_concurrent=self.max_concurrent)

    async def stop_workers(self) -> None:
        """Stop all worker tasks."""
        self._running = False
        for w in self._workers:
            w.cancel()
        self._workers.clear()
        logger.info("orchestrator_queue_stopped")

    async def enqueue(
        self,
        message: str,
        user_id: str = "default_user",
        conversation_id: Optional[str] = None,
    ) -> QueuedTask:
        """Enqueue an orchestrator request.

        Returns the QueuedTask with position info.
        Raises asyncio.QueueFull if queue is at capacity.
        """
        task = QueuedTask(
            message=message,
            user_id=user_id,
            conversation_id=conversation_id,
            position=self._queue.qsize() + 1,
        )

        try:
            self._queue.put_nowait(task)
        except asyncio.QueueFull:
            task.status = QueuedTaskStatus.FAILED
            task.error = f"Queue full ({self.max_queue_size} pending). Try again later."
            return task

        self._tasks[task.id] = task
        logger.info(
            "task_enqueued",
            task_id=task.id,
            position=task.position,
            queue_size=self._queue.qsize(),
        )
        return task

    async def enqueue_and_wait(
        self,
        message: str,
        user_id: str = "default_user",
        conversation_id: Optional[str] = None,
        timeout: float = 300,  # 5 minutes max wait
    ) -> QueuedTask:
        """Enqueue and wait for result (blocking).

        Used for synchronous API endpoints that need to return the result.
        """
        task = await self.enqueue(message, user_id, conversation_id)
        if task.status == QueuedTaskStatus.FAILED:
            return task

        # Wait for completion
        deadline = time.time() + timeout
        while time.time() < deadline:
            if task.status in (QueuedTaskStatus.COMPLETED, QueuedTaskStatus.FAILED):
                return task
            await asyncio.sleep(0.1)

        # Timeout
        task.status = QueuedTaskStatus.FAILED
        task.error = f"Timeout after {timeout}s waiting for orchestrator"
        return task

    def get_task_status(self, task_id: str) -> Optional[QueuedTask]:
        """Get status of a queued task."""
        return self._tasks.get(task_id)

    def get_queue_stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        return {
            "queue_size": self.queue_size,
            "active_tasks": self._active_count,
            "max_concurrent": self.max_concurrent,
            "max_queue_size": self.max_queue_size,
            "total_processed": self._total_processed,
            "estimated_wait_seconds": self._estimate_wait(),
        }

    def _estimate_wait(self) -> int:
        """Estimate wait time for a new task in seconds."""
        if self._active_count < self.max_concurrent:
            return 0
        # Rough estimate: 30s per task in queue / concurrent workers
        pending = self.queue_size
        avg_task_time = 30  # seconds, rough estimate
        return max(0, (pending * avg_task_time) // self.max_concurrent)

    async def _worker_loop(self, worker_name: str) -> None:
        """Background worker that picks tasks from queue and executes them.

        BUG-M6 FIX: Wraps semaphore acquisition in try/except CancelledError
        to re-queue the task if cancelled between get() and semaphore acquire.
        This prevents task loss during worker shutdown.
        """
        while self._running:
            try:
                # Wait for a task from the queue
                task = await asyncio.wait_for(self._queue.get(), timeout=50.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            # FIX: HIGH-03 - Track whether task was consumed (completed/failed)
            # vs re-queued (CancelledError). task_done() must NOT be called
            # when re-queuing, as the task is back in the queue.
            _task_consumed = False
            try:
                # Acquire semaphore (controls concurrency)
                async with self._semaphore:
                    self._active_count += 1
                    task.status = QueuedTaskStatus.RUNNING
                    task.started_at = time.time()
                    task.position = 0

                    logger.info(
                        "task_started",
                        task_id=task.id,
                        worker=worker_name,
                        active=self._active_count,
                    )

                    try:
                        result = await self._execute_task(task)
                        task.result = result
                        task.status = QueuedTaskStatus.COMPLETED
                    except Exception as e:
                        task.error = str(e)
                        task.status = QueuedTaskStatus.FAILED
                        logger.error("task_failed", task_id=task.id, error=str(e))
                    finally:
                        task.completed_at = time.time()
                        self._active_count -= 1
                        self._total_processed += 1

                        duration = (task.completed_at - (task.started_at or task.created_at))
                        logger.info(
                            "task_completed",
                            task_id=task.id,
                            status=task.status,
                            duration_s=f"{duration:.1f}",
                        )

                        # Cleanup old tasks (keep last 100)
                        if len(self._tasks) > 100:
                            oldest = sorted(self._tasks.values(), key=lambda t: t.created_at)
                            for old in oldest[:len(self._tasks) - 100]:
                                if old.status in (QueuedTaskStatus.COMPLETED, QueuedTaskStatus.FAILED):
                                    self._tasks.pop(old.id, None)
                _task_consumed = True
            except asyncio.CancelledError:
                # Re-queue the task before propagating cancellation
                await self._queue.put(task)
                raise
            finally:
                if _task_consumed:
                    self._queue.task_done()

    async def _execute_task(self, task: QueuedTask) -> dict[str, Any]:
        """Execute a queued task through the orchestrator."""
        from app.core.dependencies import _session_factory
        from app.services.orchestrator import CoreOrchestrator

        if not _session_factory:
            raise RuntimeError("Database not initialized")

        async with _session_factory() as db:
            orchestrator = CoreOrchestrator(db)
            result = await orchestrator.process_user_request(
                message=task.message,
                user_id=task.user_id,
                conversation_id=task.conversation_id,
            )
            await db.commit()
            return result


# Module-level singleton
_queue: Optional[OrchestratorQueue] = None


def get_orchestrator_queue(
    max_concurrent: int = 3,
    max_queue_size: int = 50,
) -> OrchestratorQueue:
    """Get or create the global orchestrator queue."""
    global _queue
    if _queue is None:
        _queue = OrchestratorQueue(
            max_concurrent=max_concurrent,
            max_queue_size=max_queue_size,
        )
    return _queue
