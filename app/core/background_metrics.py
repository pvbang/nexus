"""
MekongAI Nexus - Background Task Metrics.

Lightweight in-memory singleton that tracks success/failure counts,
timestamps, and last error for fire-and-forget background tasks.
Provides observability without external dependencies.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class TaskMetric:
    """Metrics for a single background task type."""

    success_count: int = 0
    failure_count: int = 0
    last_success_time: float | None = None
    last_failure_time: float | None = None
    last_error_message: str = ""

    def record_success(self) -> None:
        self.success_count += 1
        self.last_success_time = time.time()

    def record_failure(self, error: str) -> None:
        self.failure_count += 1
        self.last_failure_time = time.time()
        self.last_error_message = error[:500]

    def to_dict(self) -> dict[str, Any]:
        return {
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "last_success_time": self.last_success_time,
            "last_failure_time": self.last_failure_time,
            "last_error_message": self.last_error_message,
        }


class BackgroundMetrics:
    """Thread-safe singleton tracking background task outcomes."""

    _instance: BackgroundMetrics | None = None
    _lock = Lock()

    def __new__(cls) -> BackgroundMetrics:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._metrics: dict[str, TaskMetric] = {}
                    cls._instance._metrics_lock = Lock()
        return cls._instance

    def record_success(self, task_name: str) -> None:
        with self._metrics_lock:
            if task_name not in self._metrics:
                self._metrics[task_name] = TaskMetric()
            self._metrics[task_name].record_success()

    def record_failure(self, task_name: str, error: str) -> None:
        with self._metrics_lock:
            if task_name not in self._metrics:
                self._metrics[task_name] = TaskMetric()
            self._metrics[task_name].record_failure(error)

    def get_metrics(self) -> dict[str, Any]:
        """Return all metrics as a serializable dict."""
        with self._metrics_lock:
            return {name: metric.to_dict() for name, metric in self._metrics.items()}

    def reset(self) -> None:
        """Reset all metrics (useful for testing)."""
        with self._metrics_lock:
            self._metrics.clear()


def get_background_metrics() -> BackgroundMetrics:
    """Get the singleton BackgroundMetrics instance."""
    return BackgroundMetrics()
