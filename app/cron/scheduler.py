"""
MekongAI Nexus - Cron Scheduler (Hermes-agent inspired).

APScheduler-based scheduler for recurring agent tasks.
Jobs execute through the orchestrator pipeline.
Delivery routing: results are sent to the user via WebSocket, Telegram, or email.

Background tick loop: when APScheduler is not installed, call
``start_background_loop()`` to poll ``tick()`` every 60 seconds via
``asyncio.create_task``.

Inspired by: hermes-agent/cron/scheduler.py + cron/jobs.py

TODO: Add a periodic cleanup job for task_events retention.
  Recommended: Register a daily cron job (e.g. "0 3 * * *") that runs:
    DELETE FROM task_events
    WHERE created_at < NOW() - INTERVAL '{settings.task_events_retention_days} days';
  See app/config.py → task_events_retention_days (default 90 days).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Awaitable, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CronJob:
    """Definition of a scheduled job."""
    job_id: str
    name: str
    cron_expression: str  # e.g. "0 9 * * *" (9 AM daily)
    task_message: str     # The message to send to orchestrator
    user_id: str = "cron_system"
    enabled: bool = True
    created_at: str = ""
    last_run_at: Optional[str] = None
    last_result: Optional[str] = None
    run_count: int = 0
    delivery_channel: str = ""   # "websocket", "telegram", "email", or "" (no delivery)
    delivery_target: str = ""    # user_id, chat_id, or email address
    metadata: dict[str, Any] = field(default_factory=dict)


class CronScheduler:
    """
    Lightweight cron scheduler for recurring agent tasks.

    Uses APScheduler if available, otherwise provides in-memory job storage
    that can be polled by a background task.

    Delivery routing: after each job completes, route the result to the
    configured channel (websocket, telegram, email).
    """

    def __init__(self) -> None:
        self._jobs: dict[str, CronJob] = {}
        self._scheduler: Any = None
        self._executor: Optional[Callable[[str, str], Awaitable[dict]]] = None
        self._bg_task: Optional[asyncio.Task] = None
        self._bg_running: bool = False

    def set_executor(self, executor: Callable[[str, str], Awaitable[dict]]) -> None:
        """Set the function that executes cron jobs (typically orchestrator.process_user_request)."""
        self._executor = executor

    async def start(self) -> bool:
        """Start the scheduler. Returns True if APScheduler is available."""
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger

            # Use Redis jobstore for persistence across restarts
            jobstores = {}
            try:
                import os
                redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
                from apscheduler.jobstores.redis import RedisJobStore
                jobstores["default"] = RedisJobStore(
                    jobs_key="nexus:cron:jobs",
                    run_times_key="nexus:cron:run_times",
                    host=redis_url.split("://")[1].split(":")[0].split("/")[0],
                    port=int(redis_url.split(":")[2].split("/")[0]) if ":" in redis_url.split("://")[1] else 6379,
                    db=int(redis_url.rsplit("/", 1)[-1]) if "/" in redis_url.split("://")[1] else 0,
                )
                logger.info("cron_jobstore_redis", redis_url=redis_url)
            except Exception as e:
                logger.info("cron_jobstore_memory", reason=str(e))

            self._scheduler = AsyncIOScheduler(jobstores=jobstores)
            self._scheduler.start()
            logger.info("cron_scheduler_started", engine="apscheduler",
                        persistent=bool(jobstores))

            # Re-register any existing enabled jobs
            for job in self._jobs.values():
                if job.enabled:
                    self._register_with_scheduler(job)

            return True
        except ImportError:
            logger.warning("apscheduler_not_installed",
                           message="Install apscheduler for cron support: pip install apscheduler")
            return False

    async def stop(self) -> None:
        """Stop the scheduler."""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("cron_scheduler_stopped")

    def create_job(
        self,
        name: str,
        cron_expression: str,
        task_message: str,
        user_id: str = "cron_system",
        delivery_channel: str = "",
        delivery_target: str = "",
        metadata: Optional[dict] = None,
    ) -> CronJob:
        """Create a new cron job with optional delivery routing."""
        job_id = f"cron_{uuid.uuid4().hex[:12]}"
        job = CronJob(
            job_id=job_id,
            name=name,
            cron_expression=cron_expression,
            task_message=task_message,
            user_id=user_id,
            created_at=datetime.utcnow().isoformat(),
            delivery_channel=delivery_channel,
            delivery_target=delivery_target,
            metadata=metadata or {},
        )
        self._jobs[job_id] = job
        logger.info("cron_job_created", job_id=job_id, name=name, cron=cron_expression,
                     delivery_channel=delivery_channel or "none")

        if self._scheduler:
            self._register_with_scheduler(job)

        return job

    def _register_with_scheduler(self, job: CronJob) -> None:
        """Register job with APScheduler."""
        if not self._scheduler:
            return
        try:
            from apscheduler.triggers.cron import CronTrigger
            parts = job.cron_expression.split()
            if len(parts) == 5:
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1],
                    day=parts[2], month=parts[3], day_of_week=parts[4],
                )
                self._scheduler.add_job(
                    self._execute_job, trigger,
                    args=[job.job_id],
                    id=job.job_id,
                    replace_existing=True,
                )
        except Exception as e:
            logger.error("cron_job_register_failed", job_id=job.job_id, error=str(e))

    async def _execute_job(self, job_id: str) -> None:
        """Execute a cron job through the orchestrator, then deliver the result."""
        job = self._jobs.get(job_id)
        if not job or not job.enabled:
            return

        if not self._executor:
            logger.warning("cron_no_executor", job_id=job_id)
            return

        logger.info("cron_job_executing", job_id=job_id, name=job.name)
        result: dict[str, Any] = {}
        try:
            result = await self._executor(job.task_message, job.user_id)
            job.last_run_at = datetime.utcnow().isoformat()
            job.last_result = "success"
            job.run_count += 1
            logger.info("cron_job_completed", job_id=job_id, run_count=job.run_count)
        except Exception as e:
            job.last_run_at = datetime.utcnow().isoformat()
            job.last_result = f"error: {str(e)[:200]}"
            result = {"error": str(e), "message": f"Job {job.name} failed: {e}"}
            logger.error("cron_job_failed", job_id=job_id, error=str(e))

        # Deliver result to the configured channel
        if job.delivery_channel:
            await self._deliver_result(job, result)

    async def _deliver_result(self, job: CronJob, result: dict[str, Any]) -> None:
        """Route job result to the appropriate delivery channel.

        Supported channels:
        - ``"websocket"``: broadcast via :class:`ConnectionManager`
        - ``"telegram"``: send via :class:`ChannelManager` (OutboundMessage)
        - ``"email"``: log-only placeholder (no email provider wired yet)
        """
        channel = job.delivery_channel.lower().strip()
        target = job.delivery_target or job.user_id
        message_text = result.get("message", str(result))

        logger.info(
            "cron_delivery_attempt",
            job_id=job.job_id,
            channel=channel,
            target=target,
        )

        try:
            if channel == "websocket":
                await self._deliver_websocket(job, target, message_text, result)
            elif channel == "telegram":
                await self._deliver_telegram(job, target, message_text)
            elif channel == "email":
                await self._deliver_email(job, target, message_text)
            else:
                logger.warning("cron_delivery_unknown_channel",
                               job_id=job.job_id, channel=channel)
        except Exception as exc:
            logger.error("cron_delivery_failed",
                         job_id=job.job_id, channel=channel, error=str(exc))

    # ------------------------------------------------------------------
    # Delivery channel implementations
    # ------------------------------------------------------------------

    async def _deliver_websocket(
        self, job: CronJob, target: str, message_text: str, result: dict[str, Any],
    ) -> None:
        """Broadcast cron result via WebSocket ConnectionManager."""
        from app.api.ws import manager as ws_manager

        payload = {
            "type": "cron_result",
            "job_id": job.job_id,
            "job_name": job.name,
            "target": target,
            "message": message_text,
            "result": result,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Try user-specific channel first, then broadcast
        user_channel = f"chat:{target}"
        if user_channel in ws_manager.active_connections:
            await ws_manager.send_to_channel(user_channel, payload)
        else:
            await ws_manager.broadcast(payload)

        logger.info("cron_delivered_websocket", job_id=job.job_id, target=target)

    async def _deliver_telegram(
        self, job: CronJob, target: str, message_text: str,
    ) -> None:
        """Send cron result via Telegram through ChannelManager."""
        from app.channels.manager import get_channel_manager
        from app.channels.base import OutboundMessage

        cm = get_channel_manager()
        outbound = OutboundMessage(
            channel_type="telegram",
            channel_id=target,  # Telegram chat_id
            content=f"🕒 **Cron Job: {job.name}**\n\n{message_text}",
            metadata={"job_id": job.job_id, "source": "cron_scheduler"},
        )
        await cm.send(outbound)
        logger.info("cron_delivered_telegram", job_id=job.job_id, chat_id=target)

    async def _deliver_email(
        self, job: CronJob, target: str, message_text: str,
    ) -> None:
        """Placeholder for email delivery — logs the attempt.

        Wire up an SMTP / SendGrid / SES provider here when needed.
        """
        logger.info(
            "cron_delivery_email_placeholder",
            job_id=job.job_id,
            email=target,
            message_preview=message_text[:200],
        )

    # ------------------------------------------------------------------
    # Background tick loop (fallback when APScheduler is not installed)
    # ------------------------------------------------------------------

    async def tick(self) -> None:
        """Evaluate all enabled jobs and execute any that are due.

        This is a simple poll-based fallback for environments without
        APScheduler.  When APScheduler **is** running, ``tick()`` is a
        no-op because APScheduler handles scheduling internally.
        """
        if self._scheduler is not None:
            return  # APScheduler handles scheduling — nothing to do

        now = datetime.utcnow()
        for job in list(self._jobs.values()):
            if not job.enabled:
                continue
            if self._is_due(job, now):
                try:
                    await self._execute_job(job.job_id)
                except Exception as exc:
                    logger.error("tick_job_error", job_id=job.job_id, error=str(exc))

    @staticmethod
    def _is_due(job: CronJob, now: datetime) -> bool:
        """Check if a job is due based on its cron expression (minute-level).

        Only matches when the job has not already run in the current minute.
        Supports standard 5-field cron expressions with ``*`` wildcards.
        """
        parts = job.cron_expression.split()
        if len(parts) != 5:
            return False

        minute, hour, day, month, dow = parts

        def _match(field: str, value: int) -> bool:
            if field == "*":
                return True
            try:
                return int(field) == value
            except ValueError:
                return False

        if not all([
            _match(minute, now.minute),
            _match(hour, now.hour),
            _match(day, now.day),
            _match(month, now.month),
            _match(dow, now.weekday()),  # 0=Monday in Python
        ]):
            return False

        # Prevent duplicate runs within the same minute
        if job.last_run_at:
            try:
                last = datetime.fromisoformat(job.last_run_at)
                if (
                    last.year == now.year
                    and last.month == now.month
                    and last.day == now.day
                    and last.hour == now.hour
                    and last.minute == now.minute
                ):
                    return False
            except ValueError:
                pass

        return True

    async def start_background_loop(self, interval: float = 60.0) -> None:
        """Start an asyncio background task that calls ``tick()`` periodically.

        Args:
            interval: Seconds between each tick (default 60).
        """
        if self._bg_running:
            logger.debug("background_loop_already_running")
            return

        self._bg_running = True
        self._bg_task = asyncio.create_task(self._background_loop(interval))
        logger.info("cron_background_loop_started", interval=interval)

    async def stop_background_loop(self) -> None:
        """Stop the background tick loop gracefully."""
        self._bg_running = False
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except asyncio.CancelledError:
                pass
            self._bg_task = None
        logger.info("cron_background_loop_stopped")

    async def _background_loop(self, interval: float) -> None:
        """Internal loop — calls ``tick()`` every *interval* seconds."""
        try:
            while self._bg_running:
                try:
                    await self.tick()
                except Exception as exc:
                    logger.warning("cron_tick_error", error=str(exc))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def delete_job(self, job_id: str) -> bool:
        """Delete a cron job."""
        if job_id not in self._jobs:
            return False
        del self._jobs[job_id]
        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
        logger.info("cron_job_deleted", job_id=job_id)
        return True

    def toggle_job(self, job_id: str, enabled: bool) -> Optional[CronJob]:
        """Enable/disable a cron job."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        job.enabled = enabled
        if self._scheduler:
            if enabled:
                self._register_with_scheduler(job)
            else:
                try:
                    self._scheduler.remove_job(job_id)
                except Exception:
                    pass
        return job

    def list_jobs(self) -> list[CronJob]:
        """List all cron jobs."""
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> Optional[CronJob]:
        return self._jobs.get(job_id)


# Singleton
_scheduler: Optional[CronScheduler] = None


def get_cron_scheduler() -> CronScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = CronScheduler()
    return _scheduler
