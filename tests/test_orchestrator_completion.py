"""Tests for NexusOrchestrator._determine_completion — the completion gate.

This method is the single point that decides whether the task loop stops. It is
pure (only reads/writes in-memory per-task dicts, no LLM), so it can be driven
directly. These tests pin its current behaviour BEFORE the planned god-object
refactor (audit item C1) so any extraction can be verified against them.

Behaviour under test (nexus_orchestrator.py:1043-1093):
  - Hard ceiling: turn_count >= _MAX_TURNS (500) → always complete.
  - Stagnation ceiling: >= stagnant_limit history points whose range
    < _MIN_PROGRESS_DELTA (3) → complete. Limit is 15 normally, 25 for security.
  - Early-complete guard: if progress jumped > 3% this turn and the LLM said
    'complete' while < 95% → keep going (return False).
  - Otherwise: trust the LLM decision (complete iff decision == 'complete').
"""

from __future__ import annotations

from app.orchestration.nexus_orchestrator import (
    _MAX_STAGNANT_TURNS,
    _MAX_STAGNANT_TURNS_SECURITY,
    _MAX_TURNS,
)
from app.orchestration.termination import evaluate_completion

# ───────────────────────── Hard ceiling: max turns ─────────────────────────


def test_max_turns_forces_completion_even_if_llm_continues(orchestrator):
    # At the absolute turn ceiling, the loop must stop regardless of LLM intent.
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=10,
        turn_count=_MAX_TURNS,
        task_id="t-maxturns",
    ) is True


def test_below_max_turns_does_not_force_completion(orchestrator):
    # One turn below the ceiling, a 'continue' decision is respected.
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=10,
        turn_count=_MAX_TURNS - 1,
        task_id="t-belowmax",
    ) is False


# ───────────────────────── Stagnation ceiling ─────────────────────────


def test_stagnation_forces_completion_when_progress_flat(orchestrator):
    task_id = "t-stagnant"
    # 15 flat points → range 0 < 3 → stagnant → force complete.
    orchestrator._progress_history_by_task[task_id] = [50] * _MAX_STAGNANT_TURNS
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=50,
        turn_count=20,
        task_id=task_id,
    ) is True


def test_no_stagnation_when_progress_still_moving(orchestrator):
    task_id = "t-moving"
    # Range across the window is 10 (>= 3) → still working → not forced.
    history = [40] * (_MAX_STAGNANT_TURNS - 1) + [50]
    orchestrator._progress_history_by_task[task_id] = history
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=50,
        turn_count=20,
        task_id=task_id,
    ) is False


def test_security_task_gets_longer_stagnation_runway(orchestrator):
    """A flat window that would stop a normal task must NOT stop a security task."""
    task_id = "t-sec"
    # Exactly the non-security limit of flat points — enough to stop a normal task,
    # but a security task needs _MAX_STAGNANT_TURNS_SECURITY (25) before it gives up.
    orchestrator._progress_history_by_task[task_id] = [50] * _MAX_STAGNANT_TURNS
    assert _MAX_STAGNANT_TURNS < _MAX_STAGNANT_TURNS_SECURITY  # guards the premise
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=50,
        turn_count=20,
        task_id=task_id,
        is_security_task=True,
    ) is False


def test_security_task_still_stops_at_its_own_ceiling(orchestrator):
    task_id = "t-sec-stop"
    orchestrator._progress_history_by_task[task_id] = [50] * _MAX_STAGNANT_TURNS_SECURITY
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=50,
        turn_count=30,
        task_id=task_id,
        is_security_task=True,
    ) is True


# ───────────────────────── Early-complete guard ─────────────────────────


def test_continues_when_progress_jumped_and_llm_says_complete(orchestrator):
    """LLM says complete at 70% but progress just jumped > 3% → give one more turn."""
    task_id = "t-climbing"
    orchestrator._progress_by_task[task_id] = 50  # last turn was 50
    assert orchestrator._determine_completion(
        llm_decision="complete",
        progress_pct=70,  # delta 20 > 3, and < 95
        turn_count=5,
        task_id=task_id,
    ) is False


def test_completes_when_progress_jumped_but_near_done(orchestrator):
    """At >= 95% the early-complete guard no longer blocks; trust 'complete'."""
    task_id = "t-near-done"
    orchestrator._progress_by_task[task_id] = 50
    assert orchestrator._determine_completion(
        llm_decision="complete",
        progress_pct=96,  # delta big but >= 95 → guard does not fire
        turn_count=5,
        task_id=task_id,
    ) is True


def test_completes_when_progress_stable_and_llm_says_complete(orchestrator):
    """Small/zero delta + 'complete' → trust the decision."""
    task_id = "t-stable-complete"
    orchestrator._progress_by_task[task_id] = 80
    assert orchestrator._determine_completion(
        llm_decision="complete",
        progress_pct=82,  # delta 2 <= 3 → guard does not fire
        turn_count=5,
        task_id=task_id,
    ) is True


# ───────────────────────── Trust the LLM (default path) ─────────────────────────


def test_trusts_continue_decision_by_default(orchestrator):
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=42,
        turn_count=3,
        task_id="t-default-continue",
    ) is False


def test_trusts_complete_decision_by_default(orchestrator):
    # A 'complete' at sufficiently HIGH progress is trusted by default. (Progress
    # is pre-seeded equal to current so the 'climbing' guard does not fire, and ≥80
    # so the CC-7 low-progress guard does not fire — this isolates the default
    # "trust the LLM" path.)
    orchestrator._progress_by_task["t-default-complete"] = 90
    assert orchestrator._determine_completion(
        llm_decision="complete",
        progress_pct=90,
        turn_count=3,
        task_id="t-default-complete",
    ) is True


def test_low_static_progress_complete_blocked_by_default(orchestrator):
    # CC-7: closing at low static progress with no blocked-by-design justification
    # must NOT be trusted (the 7688c9cc 10%+COMPLETE pathology).
    orchestrator._progress_by_task["t-low-complete"] = 10
    assert orchestrator._determine_completion(
        llm_decision="complete",
        progress_pct=10,
        turn_count=3,
        task_id="t-low-complete",
    ) is False


# ───────────── Pure evaluate_completion: reason codes (slice-2 contract) ─────────────
# The wrapper above delegates to this pure function; these lock the reason codes
# it emits so the structured logging in the wrapper stays correct.


def test_evaluate_reason_max_turns():
    d = evaluate_completion(
        llm_decision="continue", progress_pct=10, turn_count=_MAX_TURNS,
        progress_history=[], prev_progress=0,
    )
    assert d.complete is True
    assert d.reason == "max_turns"
    assert d.detail == {"turns": _MAX_TURNS}


def test_evaluate_reason_stagnation():
    d = evaluate_completion(
        llm_decision="continue", progress_pct=50, turn_count=20,
        progress_history=[50] * _MAX_STAGNANT_TURNS, prev_progress=50,
    )
    assert d.complete is True
    assert d.reason == "stagnation"
    assert d.detail["turns_stagnant"] == _MAX_STAGNANT_TURNS


def test_evaluate_reason_climbing():
    d = evaluate_completion(
        llm_decision="complete", progress_pct=70, turn_count=5,
        progress_history=[], prev_progress=50,
    )
    assert d.complete is False
    assert d.reason == "climbing"
    assert d.detail == {"progress_pct": 70, "progress_delta": 20}


def test_evaluate_reason_llm_decision():
    d = evaluate_completion(
        llm_decision="complete", progress_pct=82, turn_count=5,
        progress_history=[], prev_progress=80,
    )
    assert d.complete is True
    assert d.reason == "llm_decision"


def test_evaluate_max_turns_precedence_over_stagnation():
    # Both ceilings would fire; max_turns is checked first and wins.
    d = evaluate_completion(
        llm_decision="continue", progress_pct=50, turn_count=_MAX_TURNS,
        progress_history=[50] * _MAX_STAGNANT_TURNS, prev_progress=50,
    )
    assert d.reason == "max_turns"


# ─────────────── Token Governor: per-task budget honest halt ───────────────
# These pin the synthesis-turn-then-halt logic wired into _determine_completion:
# when the per-task cumulative-effort budget hits hard_stop, the orchestrator
# grants exactly ONE synthesis turn (first hit falls through to normal eval),
# then terminates honestly on the SECOND hit.


def test_token_budget_grants_one_synthesis_turn_then_halts(orchestrator):
    from app.core.token_governor import get_task_budget, clear_task_budget
    from app.config import get_settings

    task_id = "t-budget-halt"
    clear_task_budget(task_id)
    try:
        # Drive the per-task budget into hard_stop (>= 95% of task_token_budget).
        limit = int(get_settings().task_token_budget)
        get_task_budget(task_id, limit).add({"total_tokens": int(limit * 0.96)})

        # First hard_stop hit with llm_decision='continue': the budget block must
        # GRANT one synthesis turn (fall through to normal eval → not complete).
        first = orchestrator._determine_completion(
            llm_decision="continue",
            progress_pct=40,
            turn_count=8,
            task_id=task_id,
        )
        assert first is False  # synthesis turn granted, loop continues once

        # Second hard_stop hit: the synthesis turn was already used → honest halt.
        second = orchestrator._determine_completion(
            llm_decision="continue",
            progress_pct=40,
            turn_count=9,
            task_id=task_id,
        )
        assert second is True  # terminated despite 'continue' + low progress
    finally:
        clear_task_budget(task_id)


def test_token_budget_synthesis_flag_does_not_leak_across_tasks(orchestrator):
    # Regression guard for the cross-task leak fix (commit fc79e653): the
    # synthesis flag is keyed per task_id, so task B exhausting its budget after
    # task A still gets its OWN synthesis turn (not denied by task A's flag).
    from app.core.token_governor import get_task_budget, clear_task_budget
    from app.config import get_settings

    task_a, task_b = "t-budget-A", "t-budget-B"
    for t in (task_a, task_b):
        clear_task_budget(t)
    try:
        limit = int(get_settings().task_token_budget)
        get_task_budget(task_a, limit).add({"total_tokens": int(limit * 0.96)})

        # Task A: burn its one synthesis turn, then halt (second hit).
        assert orchestrator._determine_completion(
            llm_decision="continue", progress_pct=40, turn_count=8, task_id=task_a,
        ) is False
        assert orchestrator._determine_completion(
            llm_decision="continue", progress_pct=40, turn_count=9, task_id=task_a,
        ) is True

        # Task B exhausts its budget LATER. Its FIRST hard_stop must still grant a
        # synthesis turn (False), proving the flag did not leak from task A.
        get_task_budget(task_b, limit).add({"total_tokens": int(limit * 0.96)})
        assert orchestrator._determine_completion(
            llm_decision="continue", progress_pct=40, turn_count=8, task_id=task_b,
        ) is False
    finally:
        for t in (task_a, task_b):
            clear_task_budget(t)


def test_determine_completion_stops_on_plateau(orchestrator):
    """Plateau detection: 20 turns with no new peak → reconciliation turn, then hard-stop."""
    tid = "t-plateau"
    # 92 is the peak (turn 0), then it wobbles 88<->91 for 20 turns. The wobble
    # defeats the stagnation gate (range 3 in the window), but plateau sees that
    # progress has not exceeded its high-water-mark for 20 turns → terminal.
    history = [92] + [88, 91] * 10  # 21 points total: peak + 20 turns of wobble
    orchestrator._progress_history_by_task[tid] = history
    orchestrator._progress_by_task[tid] = 91
    # First plateau hit grants reconciliation turn (Task 9 feature).
    first = orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=91,
        turn_count=163,
        task_id=tid,
    )
    assert first is False  # reconciliation turn granted
    # Second hit stops.
    second = orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=91,
        turn_count=164,
        task_id=tid,
    )
    assert second is True


def test_plateau_grants_one_reconciliation_turn_then_stops(orchestrator):
    """Plateau force-stop grants exactly one reconciliation turn before hard-stop."""
    tid = "t-reconcile"
    # Use [92] + [88, 91] * 10 which has range=3 in last-15 window (defeats stagnation)
    # but triggers plateau (no new peak for 20 turns).
    history = [92] + [88, 91] * 10
    orchestrator._progress_history_by_task[tid] = history
    orchestrator._progress_by_task[tid] = 91
    # First plateau hit → one reconciliation turn (False), flag set.
    first = orchestrator._determine_completion(
        llm_decision="continue", progress_pct=91, turn_count=163, task_id=tid,
    )
    assert first is False
    assert orchestrator._reconciliation_used_by_task.get(tid) is True
    # Second plateau hit → hard stop.
    second = orchestrator._determine_completion(
        llm_decision="continue", progress_pct=91, turn_count=164, task_id=tid,
    )
    assert second is True


# ─────────── Blocked-justified + stagnant → terminal (audit fb1a7a3c) ───────────
# A genuine safety/authorization refusal (or any blocked-by-design objective) reaches
# Definition-of-Done at turn 1-2 and sits at a STABLE high progress. The LLM
# orchestrator kept emitting decision="continue" (re-issuing the prohibited request
# under reframings), so trusting llm_decision looped 15 turns. When the task is both
# blocked-justified AND has stopped moving, the loop MUST stop regardless of the LLM
# decision — this is the EXPECTED terminal signature, not stagnation to break through.


def test_evaluate_blocked_and_stagnant_terminates_despite_continue():
    # fb1a7a3c shape: 100%-stable refusal, security task, LLM says "continue".
    d = evaluate_completion(
        llm_decision="continue",
        progress_pct=100,
        turn_count=8,
        progress_history=[100] * 6,
        prev_progress=100,
        is_security_task=True,
        has_blocked_justification=True,
        is_safety_refusal=True,
    )
    assert d.complete is True
    # H2 (audit 2026-06-29): a genuine safety refusal terminal names itself
    # "lawful_refusal" so the orchestrator reports it as 100% correctly-refused.
    assert d.reason == "lawful_refusal"


def test_evaluate_non_safety_block_terminates_as_blocked_terminal():
    # H2a fix: a NON-refusal block (infeasible / external) stays "blocked_terminal".
    d = evaluate_completion(
        llm_decision="continue",
        progress_pct=60,
        turn_count=8,
        progress_history=[60] * 6,
        prev_progress=60,
        has_blocked_justification=True,
        is_safety_refusal=False,
    )
    assert d.complete is True
    assert d.reason == "blocked_terminal"


def test_evaluate_blocked_but_still_climbing_does_not_terminate():
    # If a blocked-justified task is STILL making progress, don't force-stop —
    # the team may still be producing the lawful-alternative deliverable.
    d = evaluate_completion(
        llm_decision="continue",
        progress_pct=60,
        turn_count=4,
        progress_history=[20, 40, 60],
        prev_progress=40,
        is_security_task=True,
        has_blocked_justification=True,
    )
    assert d.complete is False


def test_evaluate_not_blocked_stable_high_progress_still_trusts_llm():
    # Without a blocked justification, a stable-but-not-stagnant-window task with
    # llm_decision="continue" must NOT be force-completed by the new ceiling —
    # it keeps running (guards against over-eager termination of normal tasks).
    d = evaluate_completion(
        llm_decision="continue",
        progress_pct=100,
        turn_count=6,
        progress_history=[100] * 4,
        prev_progress=100,
        is_security_task=False,
        has_blocked_justification=False,
    )
    assert d.complete is False
