"""Per-turn budget wrap-up message must scope to THIS turn, not the whole task (E2).

After E1 made the per-agent budget a PER-TURN ceiling, the hard-stop message still
read "You have consumed ~95% of your token budget ... provide your final output
NOW". An agent reads that as "the task is over" and writes a give-up conclusion —
exactly the honest-paralysis seen in run 1894f0ef, where agents kept declaring the
task infeasible turn after turn instead of just concluding the CURRENT turn.

`_turn_budget_hardstop_message` states plainly that only THIS react-loop's budget
is spent, that the orchestrator will continue the task on later turns, and that
the agent should conclude THIS turn's work (not abandon the objective).
"""

from __future__ import annotations

from app.orchestration.agent_worker import _turn_budget_hardstop_message


def test_hardstop_message_scopes_to_this_turn_not_the_task():
    msg = _turn_budget_hardstop_message(used=390_000, limit=400_000,
                                        assignment="Design the reactor",
                                        success_criteria="Sizing closes <5%")
    low = msg.lower()
    # It must signal a TURN-level wrap-up, never a task-level termination.
    assert "this turn" in low or "this react-loop" in low or "this round" in low
    # still asks the agent to CONCLUDE this turn's work
    assert "summarize" in low or "conclude" in low or "hand off" in low
    # It must NOT tell the agent the whole task/objective is finished or infeasible.
    for forbidden in ("task is complete", "task cannot", "abandon", "give up", "infeasible"):
        assert forbidden not in low, f"message must not imply task-level failure: {forbidden!r}"
    # Carries the assignment reminder so the conclusion stays on-objective.
    assert "Design the reactor" in msg


def test_hardstop_message_includes_usage_numbers():
    msg = _turn_budget_hardstop_message(used=390_000, limit=400_000,
                                        assignment="x", success_criteria="y")
    assert "390000" in msg and "400000" in msg
