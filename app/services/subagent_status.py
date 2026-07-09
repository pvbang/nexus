"""Single-source-of-truth contract for subagent / delegation status values.

Status strings used to be scattered string literals across the delegator. A
typo ("complete" vs "completed") would pass silently and mislead the
orchestrator. This module is the one place statuses are defined and validated.

Mirrors deer-flow's ``subagents/status_contract.py`` + JSON fixture idea.
"""

from __future__ import annotations


class SubagentStatus:
    """Canonical subagent status constants."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    ERROR = "error"
    NO_EXECUTOR = "no_executor"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"

    VALID = frozenset({
        PENDING, COMPLETED, FAILED, REJECTED,
        ERROR, NO_EXECUTOR, CANCELLED, TIMED_OUT,
    })

    # Every status except PENDING means the subagent has stopped.
    TERMINAL = frozenset(VALID - {PENDING})

    @classmethod
    def is_valid(cls, status: str) -> bool:
        return status in cls.VALID
