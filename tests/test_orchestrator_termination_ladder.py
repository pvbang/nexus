"""Tests for the honest-termination ladder (audit Gói 1: A1 + A2).

Pure-policy tests for ``termination.should_terminate_for_loop`` and the new
parse-fail terminate threshold. These pin the escalation ladder that replaces
the old "warn-then-reschedule-forever" / "frozen-50%-forever" behaviour.

A1 — pipeline-level loop: the same assignment hash repeating must eventually
     TERMINATE (return None from plan_next_turn), not just prepend a warning.
A2 — evaluator unparseable for several turns must eventually force an HONEST
     completion (is_complete=True + missing_items handoff), not stay at 50%.
"""

from __future__ import annotations

from app.orchestration.termination import (
    _EVAL_PARSE_FAIL_TERMINATE_AFTER,
    _LOOP_REPEAT_TERMINATE_AT,
    should_terminate_for_loop,
)

# ───────────────────────── Loop terminate ladder (A1) ─────────────────────────


def test_no_terminate_on_first_loop_detection():
    # 3 occurrences = the "warn once, give a chance to change approach" rung.
    # Without a force-stopped agent, this is NOT yet terminal.
    assert should_terminate_for_loop(occurrences=3, has_force_stopped=False) is False


def test_no_terminate_below_warn_threshold():
    assert should_terminate_for_loop(occurrences=2, has_force_stopped=False) is False
    assert should_terminate_for_loop(occurrences=1, has_force_stopped=True) is False


def test_terminate_when_loop_repeats_past_warn():
    # Warned at 3, still looping at the terminate threshold → stop.
    assert should_terminate_for_loop(
        occurrences=_LOOP_REPEAT_TERMINATE_AT, has_force_stopped=False
    ) is True


def test_terminate_early_when_agent_force_stopped():
    # An agent that was killed for being stuck means the approach is exhausted —
    # reassigning it 3x is hopeless, so terminate one rung earlier than the
    # plain repeat ceiling.
    assert should_terminate_for_loop(occurrences=3, has_force_stopped=True) is True


def test_force_stopped_does_not_terminate_before_warn_rung():
    # The early (force-stopped) path still requires the work to have actually
    # repeated (>=3); a single force-stop on a fresh assignment is not a loop.
    assert should_terminate_for_loop(occurrences=2, has_force_stopped=True) is False


def test_thresholds_are_ordered():
    # Guard the premise: the warn rung (3) sits below the hard repeat ceiling,
    # and the parse-fail terminate threshold escalates past the warn rung (3).
    assert 3 < _LOOP_REPEAT_TERMINATE_AT
    assert _EVAL_PARSE_FAIL_TERMINATE_AFTER > 3
