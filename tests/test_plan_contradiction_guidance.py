"""Planner must reconcile internal request contradictions (task 3c44a93f #5c).

The 3c44a93f request both (a) asked to run a complete 6-phase engagement and end
with an executive summary, and (b) imposed a human-in-the-loop invariant of
"one phase per session, stop and ask after each phase". Those are in tension: a
single autonomous run can only ever deliver phase 1 + the question. The planner
silently produced a 6-turn plan as if it could auto-advance, then hit the HITL
wall every turn and stalled.

This pins that the planning prompt now tells the planner to DETECT an internal
contradiction between constraints/invariants and the goal, and to plan a path
that still makes real progress (treat a HITL checkpoint as a checkpoint, not a
hard blocker) rather than producing a self-defeating plan.
"""

from __future__ import annotations

from app.orchestration.prompts import build_orchestrator_planning_prompt


def test_planning_prompt_has_contradiction_reconciliation_guidance():
    prompt = build_orchestrator_planning_prompt(agent_catalog="(agents)")
    low = prompt.lower()
    # Mentions reconciling internal contradictions between constraints and goal.
    assert "contradict" in low or "tension" in low or "conflict" in low
    # And steers toward still making progress, not stalling / refusing.
    assert "progress" in low


def test_planning_prompt_does_not_mandate_forcing():
    """Tone check: the guidance must not order 'must test' / coercive language —
    it reconciles toward feasible progress, it does not force offensive action."""
    prompt = build_orchestrator_planning_prompt(agent_catalog="(agents)")
    low = prompt.lower()
    assert "must attack" not in low
    assert "must exploit" not in low
