"""
Nexus - Checkpoint Persistence Service.

Persists ExecutionState checkpoints to PostgreSQL for crash recovery.
Enables auto-resume of tasks after server restart.

Usage:
    svc = CheckpointPersistence(session_factory)
    await svc.save_checkpoint(task_id, turn_number, state, reason="turn_complete")
    checkpoint = await svc.load_latest_checkpoint(task_id)
    resumable = await svc.get_resumable_tasks()
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, TYPE_CHECKING

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.orchestration.models import ExecutionState

logger = structlog.get_logger(__name__)


class CheckpointPersistence:
    """Persist and restore ExecutionState checkpoints to DB."""

    def __init__(self, session_factory: Callable[..., AsyncSession]) -> None:
        self._session_factory = session_factory

    async def save_checkpoint(
        self,
        task_id: str,
        turn_number: int,
        state: "ExecutionState",
        reason: str = "turn_complete",
    ) -> bool:
        """Save checkpoint to DB. Prune to keep max N checkpoints per task.

        Args:
            task_id: UUID string of the task.
            turn_number: Current turn number completed.
            state: ExecutionState to serialize.
            reason: Why checkpoint was created (turn_complete, graceful_shutdown, pre_llm_call).

        Returns:
            True if the checkpoint was persisted, False on any failure. Never
            raises — callers rely on the bool to report honestly instead of
            assuming success (e.g. a wrong task_id trips the FK to tasks.id).
        """
        from app.config import get_settings

        settings = get_settings()
        max_checkpoints = settings.task_checkpoint_max_per_task

        state_json = self._serialize_state(state)

        try:
            async with self._session_factory() as db:
                from app.models.database import TaskCheckpointModel
                import uuid

                checkpoint = TaskCheckpointModel(
                    id=uuid.uuid4(),
                    task_id=uuid.UUID(task_id),
                    turn_number=turn_number,
                    state_json=state_json,
                    reason=reason,
                )
                db.add(checkpoint)
                await db.flush()

                # Prune old checkpoints — keep only the most recent N
                subq = (
                    select(TaskCheckpointModel.id)
                    .where(TaskCheckpointModel.task_id == uuid.UUID(task_id))
                    .order_by(TaskCheckpointModel.created_at.desc())
                    .offset(max_checkpoints)
                )
                result = await db.execute(subq)
                old_ids = [row[0] for row in result.fetchall()]

                if old_ids:
                    await db.execute(
                        delete(TaskCheckpointModel).where(
                            TaskCheckpointModel.id.in_(old_ids)
                        )
                    )

                await db.commit()

            logger.debug(
                "checkpoint_saved",
                task_id=task_id[:8],
                turn=turn_number,
                reason=reason,
            )
            return True
        except Exception as e:
            logger.warning(
                "checkpoint_save_failed",
                task_id=task_id[:8],
                turn=turn_number,
                error=str(e),
            )
            return False

    async def load_latest_checkpoint(self, task_id: str) -> Optional[dict]:
        """Load the most recent checkpoint for a task.

        Returns:
            Dict with keys: task_id, turn_number, state_json, reason, created_at.
            None if no checkpoint exists.
        """
        try:
            async with self._session_factory() as db:
                from app.models.database import TaskCheckpointModel
                import uuid

                result = await db.execute(
                    select(TaskCheckpointModel)
                    .where(TaskCheckpointModel.task_id == uuid.UUID(task_id))
                    .order_by(TaskCheckpointModel.created_at.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()

                if row is None:
                    return None

                return {
                    "task_id": str(row.task_id),
                    "turn_number": row.turn_number,
                    "state_json": row.state_json,
                    "reason": row.reason,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
        except Exception as e:
            logger.warning("checkpoint_load_failed", task_id=task_id[:8], error=str(e))
            return None

    async def load_checkpoint_at_turn(self, task_id: str, turn_number: int) -> Optional[dict]:
        """Load the most recent checkpoint at or before ``turn_number``.

        This is the rollback target selector: to roll a task back to turn N,
        first load the state captured at the latest checkpoint whose
        ``turn_number <= N``. Returns None if no such checkpoint exists.
        """
        try:
            async with self._session_factory() as db:
                from app.models.database import TaskCheckpointModel
                import uuid

                result = await db.execute(
                    select(TaskCheckpointModel)
                    .where(
                        TaskCheckpointModel.task_id == uuid.UUID(task_id),
                        TaskCheckpointModel.turn_number <= turn_number,
                    )
                    .order_by(TaskCheckpointModel.turn_number.desc(),
                              TaskCheckpointModel.created_at.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return None
                return {
                    "task_id": str(row.task_id),
                    "turn_number": row.turn_number,
                    "state_json": row.state_json,
                    "reason": row.reason,
                }
        except Exception as e:
            logger.warning("checkpoint_load_at_turn_failed", task_id=task_id[:8],
                           turn=turn_number, error=str(e))
            return None

    async def rollback_to_turn(self, task_id: str, turn_number: int) -> bool:
        """Discard all checkpoints captured AFTER ``turn_number`` for a task.

        After rolling state back to ``turn_number`` (via
        :meth:`load_checkpoint_at_turn`), later checkpoints are stale and must be
        removed so a subsequent ``load_latest_checkpoint`` returns the rolled-back
        state. Returns True on success, False on error.
        """
        try:
            async with self._session_factory() as db:
                from app.models.database import TaskCheckpointModel
                import uuid

                await db.execute(
                    delete(TaskCheckpointModel).where(
                        TaskCheckpointModel.task_id == uuid.UUID(task_id),
                        TaskCheckpointModel.turn_number > turn_number,
                    )
                )
                await db.commit()
            logger.info("checkpoint_rolled_back", task_id=task_id[:8], to_turn=turn_number)
            return True
        except Exception as e:
            logger.warning("checkpoint_rollback_failed", task_id=task_id[:8],
                           turn=turn_number, error=str(e))
            return False

    async def get_resumable_tasks(self) -> List[dict]:
        """Find tasks in a resumable state (see RESUMABLE_STATES) that have checkpoints.

        Returns:
            List of dicts with: task_id, turn_number, state_json, reason, task_title.
        """
        try:
            async with self._session_factory() as db:
                from app.models.database import TaskCheckpointModel, TaskModel
                from app.core.cancel_signal import RESUMABLE_STATES
                import uuid

                # Find tasks in resumable states that have checkpoints
                # PostgreSQL DISTINCT ON requires ORDER BY to start with the same expression
                stmt = (
                    select(
                        TaskCheckpointModel.task_id,
                        TaskCheckpointModel.turn_number,
                        TaskCheckpointModel.state_json,
                        TaskCheckpointModel.reason,
                        TaskModel.title,
                        TaskModel.session_id,
                        TaskModel.user_id,
                    )
                    .join(TaskModel, TaskModel.id == TaskCheckpointModel.task_id)
                    .where(TaskModel.status.in_(list(RESUMABLE_STATES)))
                    .order_by(TaskCheckpointModel.task_id, TaskCheckpointModel.created_at.desc())
                    .distinct(TaskCheckpointModel.task_id)
                )

                result = await db.execute(stmt)
                rows = result.fetchall()

                resumable = []
                seen_task_ids = set()
                for row in rows:
                    tid = str(row.task_id)
                    if tid in seen_task_ids:
                        continue
                    seen_task_ids.add(tid)
                    resumable.append({
                        "task_id": tid,
                        "turn_number": row.turn_number,
                        "state_json": row.state_json,
                        "reason": row.reason,
                        "task_title": row.title,
                        "session_id": row.session_id,
                        "user_id": row.user_id,
                    })

                return resumable
        except Exception as e:
            logger.warning("get_resumable_tasks_failed", error=str(e))
            return []

    async def cleanup_checkpoints(self, task_id: str) -> None:
        """Delete all checkpoints for a completed task."""
        try:
            async with self._session_factory() as db:
                from app.models.database import TaskCheckpointModel
                import uuid

                await db.execute(
                    delete(TaskCheckpointModel).where(
                        TaskCheckpointModel.task_id == uuid.UUID(task_id)
                    )
                )
                await db.commit()

            logger.debug("checkpoints_cleaned", task_id=task_id[:8])
        except Exception as e:
            logger.warning("checkpoint_cleanup_failed", task_id=task_id[:8], error=str(e))

    async def mark_task_suspended(self, task_id: str) -> bool:
        """Mark a task as 'suspended' in DB (for graceful shutdown).

        Returns True if a matching task row was updated, False otherwise
        (no such task, or DB error). Never raises.
        """
        try:
            async with self._session_factory() as db:
                from app.models.database import TaskModel
                import uuid

                result = await db.execute(
                    update(TaskModel)
                    .where(TaskModel.id == uuid.UUID(task_id))
                    .values(status="suspended")
                )
                await db.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.warning("mark_suspended_failed", task_id=task_id[:8], error=str(e))
            return False

    @staticmethod
    def _serialize_state(state: "ExecutionState") -> dict:
        """Serialize ExecutionState to JSON-compatible dict.

        FIX D2: Include evolution suggestions, progress history, and objective
        tracking so resumed tasks retain full strategic context.
        """
        # Extract evolution suggestions from completed turns
        evolution_suggestions: list[str] = []
        for tr in state.turns_completed[-10:]:
            if hasattr(tr, "evolution_insight") and tr.evolution_insight:
                ei = tr.evolution_insight
                if hasattr(ei, "improvement_suggestions"):
                    evolution_suggestions.extend(ei.improvement_suggestions[:3])

        return {
            "task_id": state.task_id,
            "user_message": state.user_message,
            "current_turn": state.current_turn,
            "total_agents": state.total_agents,
            "status": state.status,
            "is_complete": state.is_complete,
            "complexity": state.complexity,
            "task_type": state.task_type,
            "success_criteria": state.success_criteria,
            "collected_data_cache": state.collected_data_cache[:20],
            "failure_tracker": state.failure_tracker,
            "turn_count": state.turn_count,
            # FIX D2: Additional fields for robust resume
            "progress_history": state.progress_history,
            "objective_tracking": state.objective_tracking[:30],
            "evolution_suggestions": evolution_suggestions[-10:],
        }
