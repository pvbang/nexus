"""
Nexus - Tool Execution Guards.

Inspired by Roo-Code's ToolRepetitionDetector and consecutive mistake tracking.
Provides safety mechanisms to detect and handle:
- Agent tool call loops (same tool + same params repeated)
- Consecutive errors without progress
- Premature task completion with open subtasks

These guards enhance existing swarm/react execution without replacing it.
"""

import hashlib
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Tool Repetition Detection
# ══════════════════════════════════════════════════════════════════════


class RepetitionAction(str, Enum):
    """Action to take when repetition is detected."""
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


@dataclass
class ToolCallRecord:
    """Record of a single tool call for repetition tracking."""
    tool_name: str
    params_hash: str
    timestamp: float = field(default_factory=time.time)


class ToolRepetitionDetector:
    """
    Detect when an agent is stuck in a loop calling the same tool repeatedly.

    Tracks recent tool calls and flags when the same tool+params combination
    appears more than `max_repetitions` times within the `window_size`.

    Port of Roo-Code's ToolRepetitionDetector pattern adapted for Python.
    """

    def __init__(
        self,
        max_repetitions: int = 100,
        window_size: int = 100,
    ):
        self._max_repetitions = max_repetitions
        self._window_size = window_size
        self._history: deque[ToolCallRecord] = deque(maxlen=window_size)

    @staticmethod
    def _hash_params(params: dict[str, Any]) -> str:
        """Create a stable hash of tool parameters for comparison."""
        # Sort keys for deterministic hashing
        normalized = str(sorted(params.items())) if params else ""
        return hashlib.md5(normalized.encode(), usedforsecurity=False).hexdigest()[:12]

    def record_call(self, tool_name: str, params: dict[str, Any]) -> RepetitionAction:
        """
        Record a tool call and check for repetition.

        Returns:
            RepetitionAction indicating whether the call should be allowed,
            warned about, or blocked.
        """
        params_hash = self._hash_params(params)
        record = ToolCallRecord(tool_name=tool_name, params_hash=params_hash)
        self._history.append(record)

        # Count matching calls in window
        match_count = sum(
            1 for r in self._history
            if r.tool_name == tool_name and r.params_hash == params_hash
        )

        if match_count >= self._max_repetitions:
            logger.warning(
                "tool_repetition_detected",
                tool=tool_name,
                repetitions=match_count,
                max_allowed=self._max_repetitions,
            )
            return RepetitionAction.BLOCK

        if match_count >= self._max_repetitions - 1:
            return RepetitionAction.WARN

        return RepetitionAction.ALLOW

    def reset(self) -> None:
        """Reset detection state (e.g., after strategy change)."""
        self._history.clear()

    @property
    def is_stuck(self) -> bool:
        """Check if the agent appears stuck based on recent history."""
        if len(self._history) < self._max_repetitions:
            return False
        # Check if last N calls are all identical
        recent = list(self._history)[-self._max_repetitions:]
        first = recent[0]
        return all(
            r.tool_name == first.tool_name and r.params_hash == first.params_hash
            for r in recent
        )


# ══════════════════════════════════════════════════════════════════════
# Consecutive Mistake Counting
# ══════════════════════════════════════════════════════════════════════


class MistakeAction(str, Enum):
    """Action to take when mistake limit is approached/reached."""
    CONTINUE = "continue"
    WARN_AGENT = "warn_agent"
    FORCE_STRATEGY_CHANGE = "force_strategy_change"


@dataclass
class ConsecutiveMistakeTracker:
    """
    Track consecutive errors/failures for an agent.

    When an agent keeps making mistakes without progress, this tracker
    detects it and recommends corrective action. Unlike hard-stopping,
    it triggers strategy adaptation (matching Nexus's "never give up" philosophy).

    Inspired by Roo-Code's consecutiveMistakeCount pattern.
    """

    max_consecutive: int = 5
    _count: int = field(default=0, init=False)
    _total_mistakes: int = field(default=0, init=False)
    _last_error: Optional[str] = field(default=None, init=False)

    def record_mistake(self, error_description: str = "") -> MistakeAction:
        """
        Record a mistake/error and determine action.

        A 'mistake' is any tool call that:
        - Returns an error
        - Produces empty/invalid output
        - Is rejected by validation
        """
        self._count += 1
        self._total_mistakes += 1
        self._last_error = error_description

        if self._count >= self.max_consecutive:
            logger.warning(
                "consecutive_mistakes_limit",
                count=self._count,
                max=self.max_consecutive,
                last_error=error_description[:200],
            )
            return MistakeAction.FORCE_STRATEGY_CHANGE

        if self._count >= self.max_consecutive - 1:
            return MistakeAction.WARN_AGENT

        return MistakeAction.CONTINUE

    def record_success(self) -> None:
        """Reset consecutive count on successful action (total preserved)."""
        self._count = 0

    def reset(self) -> None:
        """Full reset (e.g., new task phase)."""
        self._count = 0
        self._total_mistakes = 0
        self._last_error = None

    @property
    def consecutive_count(self) -> int:
        return self._count

    @property
    def total_mistakes(self) -> int:
        return self._total_mistakes

    def get_warning_message(self) -> str:
        """Generate a system message to inject when warning the agent."""
        return (
            f"⚠️ You have made {self._count} consecutive errors. "
            f"Last error: {self._last_error or 'unknown'}. "
            "STOP and reassess your approach:\n"
            "1. Re-read the original task requirements\n"
            "2. Try a COMPLETELY DIFFERENT strategy\n"
            "3. If a tool keeps failing, use a different tool\n"
            "4. Simplify your approach — break the problem into smaller steps"
        )

    def get_strategy_change_message(self) -> str:
        """Generate a system message forcing strategy change."""
        return (
            f"🚨 CRITICAL: {self._count} consecutive failures detected. "
            "Your current approach is NOT working. You MUST:\n"
            "1. ABANDON your current strategy entirely\n"
            "2. Summarize what you've tried and why it failed\n"
            "3. Choose a fundamentally different approach\n"
            "4. If you cannot solve this sub-problem, report what you learned "
            "and move to the next actionable step"
        )


# ══════════════════════════════════════════════════════════════════════
# Task Completion Guard
# ══════════════════════════════════════════════════════════════════════


def check_subtasks_completion(
    subtasks: list[Any],
    status_field: str = "status",
) -> tuple[bool, list[str]]:
    """
    Check if all subtasks are completed before allowing parent task completion.

    Inspired by Roo-Code's preventCompletionWithOpenTodos pattern.

    Args:
        subtasks: List of subtask objects (TaskModel or dicts).
        status_field: Name of the status attribute/key.

    Returns:
        Tuple of (all_complete, list_of_incomplete_task_ids).
    """
    incomplete = []
    for task in subtasks:
        if hasattr(task, status_field):
            status = getattr(task, status_field)
        elif isinstance(task, dict):
            status = task.get(status_field, "pending")
        else:
            continue

        if status not in ("completed", "cancelled"):
            task_id = (
                getattr(task, "id", None)
                or (task.get("id") if isinstance(task, dict) else None)
                or "unknown"
            )
            incomplete.append(str(task_id))

    all_complete = len(incomplete) == 0
    return all_complete, incomplete


# ══════════════════════════════════════════════════════════════════════
# Delegation Status Enum
# ══════════════════════════════════════════════════════════════════════


class DelegationStatus(str, Enum):
    """
    Task delegation lifecycle status.

    Inspired by Roo-Code's metadata-driven delegation status tracking.
    Extends the existing TaskModel.status with delegation-specific states.
    """
    ACTIVE = "active"
    DELEGATED = "delegated"
    AWAITING_CHILD = "awaiting_child"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        """Check if a status represents a terminal state."""
        return status in (cls.COMPLETED, cls.FAILED)

    @classmethod
    def can_delegate(cls, status: str) -> bool:
        """Check if a task in this status can delegate to a child."""
        return status in (cls.ACTIVE,)
