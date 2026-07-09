"""max_rounds wrap-up message must scope to THIS turn, not the whole task (D-Z1).

Same bug family as the per-turn budget hard-stop (E2): "provide your final output
NOW" without a THIS-TURN scope makes an agent that merely hit its per-role round
limit declare the whole objective done/dead — the honest-paralysis seen in run
1894f0ef. The max_rounds wrap-up must say the same "this is a THIS-TURN limit,
the orchestrator continues the task" as _turn_budget_hardstop_message."""

from __future__ import annotations

from app.orchestration.agent_worker import _max_rounds_wrapup_message


def test_max_rounds_message_scopes_to_this_turn():
    msg = _max_rounds_wrapup_message(
        max_rounds=40, role="engineer",
        assignment="Design the reactor", success_criteria="Sizing closes <5%",
    )
    low = msg.lower()
    assert "this turn" in low or "this round" in low or "this react-loop" in low
    assert "summarize" in low or "conclude" in low or "hand off" in low
    for forbidden in ("task is complete", "task cannot", "abandon", "give up", "infeasible"):
        assert forbidden not in low, f"must not imply task-level failure: {forbidden!r}"
    assert "Design the reactor" in msg


def test_max_rounds_message_includes_limit_and_role():
    msg = _max_rounds_wrapup_message(
        max_rounds=40, role="engineer", assignment="x", success_criteria="y",
    )
    assert "40" in msg and "engineer" in msg
