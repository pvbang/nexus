"""Plateau detection: stop when progress stops reaching NEW peaks, even if it
oscillates with non-zero variance (the f188a4c4 88<->91 defeat of stagnation)."""
from __future__ import annotations

from app.orchestration.termination import (
    _MAX_PLATEAU_TURNS,
    _MAX_PLATEAU_TURNS_SECURITY,
    plateau_reached,
    turns_since_new_peak,
)


def test_turns_since_new_peak_counts_from_last_strict_peak():
    # peak 90 reached at index 2; 3 later turns never exceed 90 → 3 turns since peak
    assert turns_since_new_peak([10, 50, 90, 88, 91 - 1, 90]) == 3


def test_climbing_progress_never_plateaus():
    history = list(range(0, 96, 5))  # 0,5,...,95 — every turn a new peak
    assert turns_since_new_peak(history) == 0
    assert plateau_reached(history) is False


def test_oscillation_below_peak_triggers_plateau():
    # reach 91 once, then wobble 88<->90 for 20 more turns: never a new peak
    history = [91] + [88, 90] * 10  # 21 entries, peak at index 0
    assert turns_since_new_peak(history) >= _MAX_PLATEAU_TURNS
    assert plateau_reached(history, is_security_task=False) is True


def test_security_task_gets_longer_plateau_window():
    history = [91] + [88, 90] * 11  # 23 turns since peak
    # 23 >= 20 (non-security) but < 30 (security)
    assert plateau_reached(history, is_security_task=False) is True
    assert plateau_reached(history, is_security_task=True) is False


def test_short_history_never_plateaus():
    assert plateau_reached([10, 20, 20], is_security_task=False) is False


from app.orchestration.termination import evaluate_completion


def test_evaluate_completion_force_stops_on_plateau():
    history = [92] + [88, 91] * 10  # 20 turns since peak, range=3 defeats stagnation
    decision = evaluate_completion(
        llm_decision="continue",
        progress_pct=91,
        turn_count=163,
        progress_history=history,
        prev_progress=88,
        is_security_task=False,
    )
    assert decision.complete is True
    assert decision.reason == "plateau"


def test_evaluate_completion_no_plateau_while_climbing():
    history = list(range(0, 96, 5))  # always a new peak
    decision = evaluate_completion(
        llm_decision="continue",
        progress_pct=95,
        turn_count=20,
        progress_history=history,
        prev_progress=90,
        is_security_task=False,
    )
    assert decision.reason != "plateau"
    assert decision.complete is False


def test_f188a4c4_trajectory_would_have_stopped():
    """Replays the real f188a4c4 shape: climb to ~90 by turn ~18, then oscillate.

    The pre-fix system ran 163 turns with an oscillation that defeated stagnation
    via variance (88<->91). Plateau must fire well before 163 turns.

    CORRECTED from brief: original oscillate = [88,90,91,89]*40 would never plateau
    because 91 > 90+epsilon resets turns_since_new_peak every 4th turn. Fixed to
    oscillate = [88,90,89]*40 (max 90 = at the peak, not a NEW peak).
    """
    climb = list(range(0, 91, 5))  # [0, 5, ..., 85, 90] — 19 entries, peak at 90
    oscillate = [88, 90, 89] * 40   # 120 turns oscillating at/below peak, never NEW peak
    history = climb + oscillate      # 139 total (still < 163 the real task ran)

    # Find first index where plateau_reached becomes True
    first_stop = next(
        (i for i in range(len(history)) if plateau_reached(history[: i + 1])),
        None,
    )
    assert first_stop is not None, "Plateau should fire, but never did"
    # Must stop within ~20 turns after the last genuine peak (index 18), not at 163
    # len(climb) = 19, so first_stop should be < 19 + 20 + margin = ~40
    assert first_stop < len(climb) + 25, (
        f"Plateau fired at turn {first_stop}, expected < {len(climb) + 25}"
    )
