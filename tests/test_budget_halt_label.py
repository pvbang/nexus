"""A HALT (budget/stagnation/barren/evolution) must not be labelled "complete" (G2-7).

Root cause G2-7 (tri-run audit round-2, 2026-07-03, task-095fc772 gasifier). The
task burned 267% of its token budget → the budget ceiling in _determine_completion
returned is_complete=True with reason ``token_budget_exhausted``. But DECISIONS.md
rendered ``Decision: complete`` (pipeline.py:2016 does a bare
``"complete" if is_complete else "continue"``), so a run that HALTED at 13% with
5/8 deliverables missing reads as a 100%-successful completion — misleading the
operator and any downstream consumer.

A terminal reason that is a HALT (not a genuine DoD completion) must render as
"halted (<reason>)". Only a real completion (no terminal reason, or an explicit
lawful_refusal which IS the definition of done for its branch) reads as "complete".
"""

from __future__ import annotations

from app.orchestration.pipeline import format_decision_label


def test_budget_exhausted_renders_halted_not_complete():
    label = format_decision_label(is_complete=True, terminated_reason="token_budget_exhausted")
    assert "complete" != label
    assert "halt" in label.lower()
    assert "budget" in label.lower() or "token" in label.lower()


def test_evolution_terminate_renders_halted():
    label = format_decision_label(is_complete=True, terminated_reason="evolution_terminate")
    assert "halt" in label.lower()


def test_barren_renders_halted():
    label = format_decision_label(is_complete=True, terminated_reason="barren_no_production")
    assert "halt" in label.lower()


def test_stagnation_renders_halted():
    label = format_decision_label(is_complete=True, terminated_reason="stagnation")
    assert "halt" in label.lower()


def test_genuine_completion_renders_complete():
    # No terminal reason → a real DoD completion.
    assert format_decision_label(is_complete=True, terminated_reason="") == "complete"
    assert format_decision_label(is_complete=True, terminated_reason=None) == "complete"


def test_lawful_refusal_renders_complete():
    # A lawful refusal IS the definition of done for its branch — reads "complete".
    label = format_decision_label(is_complete=True, terminated_reason="lawful_refusal")
    assert label == "complete"


def test_not_complete_renders_continue():
    assert format_decision_label(is_complete=False, terminated_reason="") == "continue"
