"""One range-stagnation primitive, called with each consumer's own window (A2).

Arch review A2: `max(recent) - min(recent) < min_delta` was copy-pasted in ≥3 places
(completion gate window 15/25, terminal-block window 3, TaskHealth). This unifies the
COMPUTATION into termination.is_stagnant_range while each caller keeps its OWN window
— so it's DRY without changing any verdict. These tests pin the primitive AND that
each migrated caller still concludes exactly what its inline copy did.
"""

from __future__ import annotations

from app.orchestration.termination import (
    is_stagnant_range,
    _MIN_PROGRESS_DELTA,
    _MAX_STAGNANT_TURNS,
)


# ── the primitive ─────────────────────────────────────────────────────────────

def test_range_below_delta_is_stagnant():
    assert is_stagnant_range([40, 41, 40, 42], window=4, min_delta=3) is True


def test_range_at_or_above_delta_not_stagnant():
    assert is_stagnant_range([40, 45, 40], window=3, min_delta=3) is False  # range 5


def test_fewer_than_window_never_stagnant():
    # Mirrors the old `len(progress_history) >= stagnant_limit` guard.
    assert is_stagnant_range([40, 40], window=15, min_delta=3) is False


def test_empty_safe():
    assert is_stagnant_range([], window=3, min_delta=3) is False
    assert is_stagnant_range(None, window=3, min_delta=3) is False


def test_uses_only_the_last_window():
    # Old climbing turns must not hide current flatness.
    assert is_stagnant_range([0, 50, 90, 90, 90], window=3, min_delta=3) is True


# ── equivalence with the two migrated call sites ──────────────────────────────

def test_equivalence_completion_gate_window():
    # C1: len>=15 and range<3. Build a 15-long flat tail.
    flat = [50] * 15
    assert is_stagnant_range(flat, _MAX_STAGNANT_TURNS, _MIN_PROGRESS_DELTA) is True
    climbing = list(range(0, 75, 5))  # 15 points, range 70
    assert is_stagnant_range(climbing, _MAX_STAGNANT_TURNS, _MIN_PROGRESS_DELTA) is False


def test_equivalence_terminal_block_window3():
    # nexus terminal-block: recent = hist[-3:], range < 3.
    assert is_stagnant_range([12, 13, 13], 3, _MIN_PROGRESS_DELTA) is True
    assert is_stagnant_range([12, 13, 20], 3, _MIN_PROGRESS_DELTA) is False
