"""Drift-correction must reach BOTH readers, not be consumed by the first (A12).

Arch review (2026-07-03): GoalCompliance.corrective_context was a consume-once
property (read → cleared). But two places read it each turn — the orchestrator
eval-context builder (pipeline) AND the agent-context builder (turn_executor). The
first reader consumed it, so the AGENT — the one that most needs to be steered back
on goal — usually saw an empty string. Reading must be idempotent (peek); clearing
is an explicit end-of-turn step.
"""

from __future__ import annotations

from app.orchestration.goal_compliance import GoalComplianceChecker


def _gc():
    gc = GoalComplianceChecker(goal_profile=None, llm=None)
    gc._corrective_context = "[DRIFT] Refocus on the gasifier CAD deliverable."
    return gc


def test_two_reads_both_see_the_correction():
    gc = _gc()
    first = gc.corrective_context   # orchestrator eval builder
    second = gc.corrective_context  # agent context builder
    assert "DRIFT" in first
    assert "DRIFT" in second, "second reader (the agent) lost the drift correction"


def test_explicit_clear_empties_it():
    gc = _gc()
    assert gc.corrective_context
    gc.clear_corrective_context()
    assert gc.corrective_context == ""


def test_empty_when_no_drift():
    gc = GoalComplianceChecker(goal_profile=None, llm=None)
    assert gc.corrective_context == ""
