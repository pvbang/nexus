"""Stagnation detection reads SELF-progress (pre-cap), not the capped number (E-R5).

Root cause E-R5 (tri-run audit 2026-07-02). One ``progress_pct`` carried two
meanings: (A) how much the agent thinks is done (self-graded) and (B) how much is
verifiably delivered (ground-truth, capped by artifacts-on-disk). The stagnation
gate read the CAPPED number — so an artifact-cap that pinned progress low (the
gasifier 10% bug) looked identical to a genuinely stuck task, tripping runaway
busywork. Meanwhile a truly-stuck run (deepseek 20%) must STILL be caught.

Fix: the stagnation signal reads a separate ``self_progress`` history (the LLM's
pre-cap estimate). A cap that drops the DELIVERED number no longer poisons
stagnation detection; a genuinely flat self-estimate still trips it. Falls back to
the capped history when self-progress history is unavailable (turn 1 / resume) so
behavior never regresses.
"""

from __future__ import annotations

from app.orchestration.nexus_orchestrator import is_progress_stagnant


# ── cap-pinned-low but self-progress climbing → NOT stagnant (the gasifier bug) ──

def test_capped_low_but_self_progress_rising_is_not_stagnant():
    # Delivered % pinned at 10 by a (mis)cap, but the agent's own estimate climbs.
    capped = [10, 10, 10]
    self_progress = [60, 80, 92]
    assert is_progress_stagnant(self_progress, capped, min_delta=5) is False


# ── self-progress genuinely flat → stagnant (the deepseek bug must still fire) ──

def test_flat_self_progress_is_stagnant():
    capped = [20, 20, 20]
    self_progress = [20, 18, 21]  # span 3 < 5 → truly stuck
    assert is_progress_stagnant(self_progress, capped, min_delta=5) is True


def test_self_progress_rising_is_not_stagnant():
    assert is_progress_stagnant([30, 55, 80], [30, 55, 80], min_delta=5) is False


# ── fallback: no self-progress history → use the capped history (no regression) ──

def test_falls_back_to_capped_when_no_self_progress():
    # Old behavior preserved: with no self-progress data, a flat capped history is
    # still read as stagnation.
    assert is_progress_stagnant([], [10, 10, 10], min_delta=5) is True
    assert is_progress_stagnant(None, [10, 40, 75], min_delta=5) is False


# ── not enough data → never a false stagnation alarm ──

def test_too_few_points_is_not_stagnant():
    assert is_progress_stagnant([50], [50], min_delta=5) is False
    assert is_progress_stagnant([], [], min_delta=5) is False
