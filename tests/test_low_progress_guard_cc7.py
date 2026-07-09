"""P2-C (CC-7): don't trust 'complete' at low static progress without a reason.

Audit 2026-06-22 (`7688c9cc`): the LLM returned Progress 10% + Decision COMPLETE
and the gate trusted it unconditionally (`termination.py` returned
`llm_decision == "complete"`), closing a task at 10%/33% with no "blocked-by-design"
justification. `367a9793` closed at 33% + "complete" leaving 5 critic findings.

Fix: a 'complete' decision at low static progress (<80%) is only honored when a
justification is present (a terminated_reason / blocked-by-design / safety
refusal). Otherwise it is downgraded to continue. High progress and justified
low-progress completions are unaffected.
"""

from __future__ import annotations

from app.orchestration.termination import CompletionDecision, evaluate_completion


def _hist(*vals):
    return list(vals)


def test_low_progress_complete_without_justification_blocked():
    d = evaluate_completion(
        llm_decision="complete",
        progress_pct=10,
        turn_count=3,
        progress_history=_hist(10, 10),
        prev_progress=10,
    )
    assert d.complete is False
    assert d.reason == "low_progress_complete_unjustified"


def test_low_progress_complete_with_justification_allowed():
    d = evaluate_completion(
        llm_decision="complete",
        progress_pct=10,
        turn_count=3,
        progress_history=_hist(10, 10),
        prev_progress=10,
        has_blocked_justification=True,
    )
    assert d.complete is True


def test_high_progress_complete_allowed():
    d = evaluate_completion(
        llm_decision="complete",
        progress_pct=92,
        turn_count=3,
        progress_history=_hist(90, 92),
        prev_progress=90,
    )
    assert d.complete is True


def test_low_progress_continue_unaffected():
    # A 'continue' at low progress stays continue (guard only touches 'complete').
    d = evaluate_completion(
        llm_decision="continue",
        progress_pct=10,
        turn_count=3,
        progress_history=_hist(10, 10),
        prev_progress=10,
    )
    assert d.complete is False
    assert d.reason == "llm_decision"
