"""
MekongAI Nexus - Periodic System Metrics Emitter.

Emits CPU, memory, and active task/agent counts to the message bus
so the frontend dashboard receives real-time system health data.
"""

import asyncio
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class SystemMetricsEmitter:
    """Background task that periodically emits system metrics."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self, interval: float = 10.0) -> None:
        """Start emitting metrics every `interval` seconds."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(interval))
        logger.info("system_metrics_emitter_started", interval=interval)

    async def stop(self) -> None:
        """Stop the emitter."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("system_metrics_emitter_stopped")

    async def _loop(self, interval: float) -> None:
        """Internal loop that collects and publishes metrics."""
        try:
            while self._running:
                try:
                    await self._emit_metrics()
                except Exception as e:
                    logger.warning("system_metrics_emit_error", error=str(e))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _emit_metrics(self) -> None:
        """Collect system metrics and publish via MessageBus.

        Emits all fields expected by frontend realtimeStore:
        cpu, memory, active_tasks, active_agents, active_swarms,
        queue_size, cache_hit_rate, uptime_seconds, timestamp.
        """
        import time as _time

        from app.communication.message_bus import get_message_bus

        # CPU & memory via psutil (graceful fallback if not installed)
        cpu_percent = 0.0
        memory_percent = 0.0
        try:
            import psutil
            cpu_percent = psutil.cpu_percent(interval=None)
            memory_percent = psutil.virtual_memory().percent
        except ImportError:
            pass  # psutil not installed — emit zeros
        except Exception:
            pass

        # Active tasks, agents from DB (best-effort)
        active_tasks = 0
        active_agents = 0
        active_swarms = 0
        try:
            from app.core.dependencies import _session_factory
            if _session_factory:
                from sqlalchemy import select, func
                from app.models.database import TaskModel, AgentModel
                async with _session_factory() as db:
                    result = await db.execute(
                        select(func.count()).select_from(TaskModel).where(
                            TaskModel.status.in_(["executing", "pending"])
                        )
                    )
                    active_tasks = result.scalar() or 0

                    result = await db.execute(
                        select(func.count()).select_from(AgentModel).where(
                            AgentModel.status.in_(["active", "running", "busy"])
                        )
                    )
                    active_agents = result.scalar() or 0
        except Exception:
            pass

        # Queue size from task queue (best-effort)
        queue_size = 0
        try:
            from app.core.task_queue import get_orchestrator_queue
            q = get_orchestrator_queue()
            queue_size = q.queue_size if hasattr(q, 'queue_size') else 0
        except Exception:
            pass

        # Uptime (seconds since process start)
        uptime_seconds = 0
        try:
            import os
            import psutil
            proc = psutil.Process(os.getpid())
            uptime_seconds = int(_time.time() - proc.create_time())
        except Exception:
            pass

        # Cache hit rate from Redis INFO stats (best-effort)
        cache_hit_rate = 0.0
        try:
            from app.core.dependencies import get_redis
            redis = await get_redis()
            info = await redis.info("stats")
            hits = int(info.get("keyspace_hits", 0))
            misses = int(info.get("keyspace_misses", 0))
            total = hits + misses
            if total > 0:
                cache_hit_rate = round(hits / total, 4)
        except Exception:
            pass  # Redis unavailable — emit 0

        metrics = {
            "cpu": cpu_percent,
            "memory": memory_percent,
            "active_tasks": active_tasks,
            "active_agents": active_agents,
            "active_swarms": active_swarms,
            "queue_size": queue_size,
            "cache_hit_rate": cache_hit_rate,
            "uptime_seconds": uptime_seconds,
            "timestamp": int(_time.time() * 1000),
        }

        bus = await get_message_bus()
        await bus.emit_event("system_metric", metrics)


# Singleton
_emitter: Optional[SystemMetricsEmitter] = None


def get_metrics_emitter() -> SystemMetricsEmitter:
    """Get or create the singleton metrics emitter."""
    global _emitter
    if _emitter is None:
        _emitter = SystemMetricsEmitter()
    return _emitter


def collect_process_gauges() -> dict[str, float]:
    """Collect cheap, synchronous process gauges for the Prometheus /metrics scrape.

    Best-effort: every probe is guarded so a missing psutil or a probe error
    yields a partial dict rather than failing the endpoint (audit C4). Kept free
    of DB/Redis I/O so the scrape stays fast and side-effect-free.
    """
    import os
    import time as _time

    gauges: dict[str, float] = {}
    try:
        import psutil
        gauges["nexus_cpu_percent"] = float(psutil.cpu_percent(interval=None))
        gauges["nexus_memory_percent"] = float(psutil.virtual_memory().percent)
        proc = psutil.Process(os.getpid())
        gauges["nexus_uptime_seconds"] = round(_time.time() - proc.create_time(), 1)
        gauges["nexus_process_rss_bytes"] = float(proc.memory_info().rss)
    except Exception:
        pass
    return gauges
