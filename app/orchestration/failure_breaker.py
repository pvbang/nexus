"""
Nexus - Consecutive-failure circuit breaker.

Pure decision helpers (no LLM, no I/O) for stopping an agent that is thrashing —
issuing tool calls round after round where every single call fails (audit §5 P3).

This is distinct from stuck detection (which spots *repeated* actions) and from
the reflection nudge driven by `_consecutive_error_calls` (which only fires when
a reflection engine is attached). A run of all-failed rounds means the agent is
making no progress regardless of whether it is repeating itself, so it gets a
hard force-stop independent of those subsystems.
"""

from __future__ import annotations

from typing import Any

# Force-stop the agent after this many consecutive rounds in which it issued tool
# calls and ALL of them failed. Small enough to cut waste quickly, large enough
# to let a genuine "try, fail, adjust, retry" recovery play out first (the agent
# also gets 3-STRIKE / stuck guidance injected in between).
MAX_CONSECUTIVE_FAILED_ROUNDS = 5


def is_failed_round(round_tool_calls: list[dict[str, Any]]) -> bool:
    """True iff the round issued ≥1 tool call and EVERY call failed.

    A round with no tool calls (agent produced text only) is not a failure round.
    A record missing the ``success`` key defaults to success (consistent with the
    ``tc.get("success", True)`` convention used elsewhere in the worker).
    """
    if not round_tool_calls:
        return False
    return all(not tc.get("success", True) for tc in round_tool_calls)


def update_failed_round_streak(
    current_streak: int,
    round_tool_calls: list[dict[str, Any]],
) -> int:
    """Advance the consecutive-all-failed-round streak for this round.

    - All calls failed  → increment.
    - Any call succeeded → reset to 0.
    - No calls this round → unchanged (neither progress nor failure).
    """
    if not round_tool_calls:
        return current_streak
    if is_failed_round(round_tool_calls):
        return current_streak + 1
    return 0
