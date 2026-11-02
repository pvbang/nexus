"""
Nexus - Resource Monitor.

Background service monitoring system memory and CPU usage.
Emits warnings to frontend but NEVER kills running tasks.

Philosophy: Tasks are sacred. Resource pressure triggers GC and warnings,
but the system trusts the task to complete. Only user-initiated cancel stops a task.
"""

import asyncio
import gc
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class ResourceMonitor:
    """Background monitor for system resources (memory, CPU).

    Periodically checks system resources and:
    - Emits warnings to frontend via message bus
    - Triggers GC on critical memory pressure
    - Logs metrics for observability
    - NEVER kills or pauses running tasks
    """

    def __init__(
        self,
        check_interval: float = 30.0,
        warning_threshold: float = 85.0,
        critical_threshold: float = 95.0,
    ):
        self._interval = check_interval
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._last_warning_level: Optional[str] = None
        self._gc_triggered_count = 0

    async def start(self) -> None:
        """Start the resource monitoring loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop(), name="resource-monitor")
        logger.info(
            "resource_monitor_started",
            interval=self._interval,
            warning_threshold=self._warning_threshold,
            critical_threshold=self._critical_threshold,
        )

    async def stop(self) -> None:
        """Stop the resource monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("resource_monitor_stopped")

    async def _monitor_loop(self) -> None:
        """Internal monitoring loop."""
        try:
            while self._running:
                await asyncio.sleep(self._interval)
                try:
                    await self._check_resources()
                except Exception as e:
                    logger.debug("resource_check_error", error=str(e))
        except asyncio.CancelledError:
            pass

    async def _check_resources(self) -> None:
        """Check memory and CPU, emit alerts if thresholds exceeded."""
        try:
            import psutil
        except ImportError:
            return  # psutil not installed — skip monitoring

        mem = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)
        memory_percent = mem.percent

        if memory_percent >= self._critical_threshold:
            # Critical: trigger GC, emit alert, but DON'T kill tasks
            gc.collect()
            self._gc_triggered_count += 1
            logger.warning(
                "memory_critical",
                percent=memory_percent,
                cpu=cpu,
                gc_count=self._gc_triggered_count,
                available_mb=round(mem.available / 1024 / 1024),
            )
            if self._last_warning_level != "critical":
                await self._emit_resource_alert("critical", memory_percent, cpu)
                self._last_warning_level = "critical"

        elif memory_percent >= self._warning_threshold:
            logger.info(
                "memory_warning",
                percent=memory_percent,
                cpu=cpu,
                available_mb=round(mem.available / 1024 / 1024),
            )
            if self._last_warning_level != "warning":
                await self._emit_resource_alert("warning", memory_percent, cpu)
                self._last_warning_level = "warning"

        else:
            # Normal — clear warning state
            if self._last_warning_level is not None:
                self._last_warning_level = None
                # Optionally emit "recovered" notification
                await self._emit_resource_alert("normal", memory_percent, cpu)

    async def _emit_resource_alert(
        self, level: str, memory_percent: float, cpu_percent: float
    ) -> None:
        """Emit resource alert to message bus for frontend display."""
        try:
            from app.communication.message_bus import get_message_bus

            bus = await get_message_bus()
            await bus.emit_event("resource_alert", {
                "level": level,
                "memory_percent": round(memory_percent, 1),
                "cpu_percent": round(cpu_percent, 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": self._build_message(level, memory_percent),
                "action": self._build_action(level),
                "gc_triggered_count": self._gc_triggered_count,
            })
        except Exception as exc:
            logger.debug("resource_alert_emit_failed", error=str(exc))

    @staticmethod
    def _build_message(level: str, memory_percent: float) -> str:
        """Build human-readable message for resource alert."""
        if level == "critical":
            return (
                f"⚠️ Memory critical: {memory_percent:.1f}%. "
                "GC triggered. Tasks continue running."
            )
        elif level == "warning":
            return f"Memory usage high: {memory_percent:.1f}%. Monitoring closely."
        else:
            return "System resources normal."

    @staticmethod
    def _build_action(level: str) -> str:
        """Describe what action was taken."""
        if level == "critical":
            return "gc_triggered"
        elif level == "warning":
            return "monitoring"
        return "recovered"

    def get_status(self) -> dict:
        """Get current monitor status for health endpoint."""
        return {
            "running": self._running,
            "last_warning_level": self._last_warning_level,
            "gc_triggered_count": self._gc_triggered_count,
            "check_interval": self._interval,
            "warning_threshold": self._warning_threshold,
            "critical_threshold": self._critical_threshold,
        }


# ─── Singleton ───────────────────────────────────────────────

_monitor: Optional[ResourceMonitor] = None


def get_resource_monitor() -> ResourceMonitor:
    """Get the singleton ResourceMonitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = ResourceMonitor()
    return _monitor
