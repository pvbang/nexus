"""
MekongAI Nexus - Task Checkpoint for Execution Resume.

Inspired by Shannon's per-agent git checkpointing + session.json pattern.
Enables resuming long-running tasks after crash/interrupt without restarting.

Key concepts:
- Each completed step is checkpointed to disk (JSON file)
- On resume, detect which steps completed → skip them
- Partial results are preserved and reusable
- Failed steps are retried from their last checkpoint

Usage:
    checkpoint = TaskCheckpoint(task_id="task-abc")

    # Check if we can resume
    state = await checkpoint.load()
    if state:
        completed = state.completed_steps
    else:
        completed = []

    # Execute steps, checkpointing after each
    if "planning" not in completed:
        plan = await run_planning()
        await checkpoint.save_step("planning", {"plan": plan})

    if "execution" not in completed:
        result = await run_execution()
        await checkpoint.save_step("execution", {"result": result})

    # Clean up when fully done
    await checkpoint.delete()
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CheckpointState:
    """Persisted checkpoint state for a task."""

    task_id: str
    completed_steps: list[str]
    step_results: dict[str, Any]
    started_at: str
    last_checkpoint_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    attempt_count: int = 0
    total_duration_ms: float = 0.0

    def is_step_complete(self, step_name: str) -> bool:
        """Check if a specific step has been completed."""
        return step_name in self.completed_steps

    def get_step_result(self, step_name: str) -> Optional[Any]:
        """Get the saved result for a completed step."""
        return self.step_results.get(step_name)


class TaskCheckpoint:
    """
    Manages checkpoint persistence for a single task.

    Saves execution progress to JSON file. On resume, completed steps
    are skipped and their results are reused.

    File location: {checkpoint_dir}/{task_id}.checkpoint.json
    """

    def __init__(
        self,
        task_id: str,
        checkpoint_dir: str = "./workspace/checkpoints",
    ) -> None:
        self.task_id = task_id
        self._dir = Path(checkpoint_dir)
        self._path = self._dir / f"{task_id}.checkpoint.json"
        self._state: Optional[CheckpointState] = None
        self._start_time = time.monotonic()

    async def load(self) -> Optional[CheckpointState]:
        """
        Load existing checkpoint state from disk.

        Returns None if no checkpoint exists (fresh task).
        Returns CheckpointState if checkpoint found (resumable).

        BUG-M8 FIX: Uses asyncio.to_thread() for file I/O to avoid
        blocking the event loop in async context.
        """
        import asyncio

        if not await asyncio.to_thread(self._path.exists):
            return None

        try:
            raw = await asyncio.to_thread(self._path.read_text, "utf-8")
            data = json.loads(raw)

            self._state = CheckpointState(
                task_id=data["task_id"],
                completed_steps=data.get("completed_steps", []),
                step_results=data.get("step_results", {}),
                started_at=data.get("started_at", ""),
                last_checkpoint_at=data.get("last_checkpoint_at", ""),
                metadata=data.get("metadata", {}),
                attempt_count=data.get("attempt_count", 0) + 1,
                total_duration_ms=data.get("total_duration_ms", 0.0),
            )

            logger.info(
                "checkpoint_loaded",
                task_id=self.task_id,
                completed_steps=len(self._state.completed_steps),
                attempt=self._state.attempt_count,
            )
            return self._state

        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.error(
                "checkpoint_load_failed",
                task_id=self.task_id,
                error=str(e),
            )
            # Corrupted checkpoint — delete and start fresh
            self._path.unlink(missing_ok=True)
            return None

    async def save_step(
        self, step_name: str, result: Any = None, metadata: Optional[dict[str, Any]] = None
    ) -> None:
        """
        Save a completed step to the checkpoint.

        This is crash-safe: writes to temp file then renames (atomic on POSIX).
        On Windows, uses best-effort write + flush.

        Args:
            step_name: Name of the completed step.
            result: Optional result data to persist for reuse on resume.
            metadata: Optional metadata to merge into checkpoint.
        """
        now = datetime.now(timezone.utc).isoformat()
        elapsed_ms = (time.monotonic() - self._start_time) * 1000

        if self._state is None:
            self._state = CheckpointState(
                task_id=self.task_id,
                completed_steps=[],
                step_results={},
                started_at=now,
                last_checkpoint_at=now,
                attempt_count=1,
            )

        # Update state
        if step_name not in self._state.completed_steps:
            self._state.completed_steps.append(step_name)
        if result is not None:
            self._state.step_results[step_name] = result
        self._state.last_checkpoint_at = now
        self._state.total_duration_ms = elapsed_ms
        if metadata:
            self._state.metadata.update(metadata)

        # Serialize and write atomically
        data = {
            "task_id": self._state.task_id,
            "completed_steps": self._state.completed_steps,
            "step_results": self._state.step_results,
            "started_at": self._state.started_at,
            "last_checkpoint_at": self._state.last_checkpoint_at,
            "metadata": self._state.metadata,
            "attempt_count": self._state.attempt_count,
            "total_duration_ms": self._state.total_duration_ms,
        }

        await self._atomic_write(data)

        logger.debug(
            "checkpoint_saved",
            task_id=self.task_id,
            step=step_name,
            total_steps=len(self._state.completed_steps),
        )

    async def delete(self) -> None:
        """
        Delete checkpoint after successful task completion.

        Call this when the task finishes successfully so resumed tasks
        don't try to skip already-done work from a previous run.
        """
        import asyncio

        await asyncio.to_thread(self._path.unlink, True)  # missing_ok=True
        self._state = None
        logger.debug("checkpoint_deleted", task_id=self.task_id)

    def is_step_complete(self, step_name: str) -> bool:
        """Quick check: was this step already completed in a previous run?"""
        if self._state is None:
            return False
        return step_name in self._state.completed_steps

    def get_step_result(self, step_name: str) -> Optional[Any]:
        """Get cached result from a previously completed step."""
        if self._state is None:
            return None
        return self._state.step_results.get(step_name)

    @property
    def completed_steps(self) -> list[str]:
        """List of completed step names."""
        if self._state is None:
            return []
        return list(self._state.completed_steps)

    @property
    def can_resume(self) -> bool:
        """Whether there's a valid checkpoint to resume from."""
        return self._state is not None and len(self._state.completed_steps) > 0

    async def _atomic_write(self, data: dict[str, Any]) -> None:
        """Write JSON data atomically (crash-safe).

        BUG-M8 FIX: Uses asyncio.to_thread() for file I/O to avoid
        blocking the event loop in async context.
        """
        import asyncio

        await asyncio.to_thread(self._dir.mkdir, parents=True, exist_ok=True)

        tmp_path = self._path.with_suffix(".tmp")
        try:
            content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
            await asyncio.to_thread(tmp_path.write_text, content, "utf-8")
            # Atomic rename (POSIX) or best-effort overwrite (Windows)
            await asyncio.to_thread(tmp_path.replace, self._path)
        except OSError as e:
            logger.error(
                "checkpoint_write_failed",
                task_id=self.task_id,
                error=str(e),
            )
            tmp_path.unlink(missing_ok=True)
            raise


# --- Factory ---


_checkpoints: dict[str, TaskCheckpoint] = {}


def get_or_create_checkpoint(
    task_id: str, checkpoint_dir: str = "./workspace/checkpoints"
) -> TaskCheckpoint:
    """Get or create a checkpoint manager for a task."""
    if task_id not in _checkpoints:
        _checkpoints[task_id] = TaskCheckpoint(
            task_id=task_id, checkpoint_dir=checkpoint_dir
        )
    return _checkpoints[task_id]


def remove_checkpoint_tracker(task_id: str) -> None:
    """Remove checkpoint tracker from memory (after task completes)."""
    _checkpoints.pop(task_id, None)
