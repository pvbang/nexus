"""TaskHealth — single source of truth for a task's progress condition (A1/A2).

Architecture review (2026-07-03): "stagnation" was computed ≥5 times across
termination.py / nexus_orchestrator.py / evolution_agent.py from the same
progress_history with different windows/thresholds, and ~14 completion/loop gates
each re-derived their own view. There was no authoritative "is this task stuck?"
answer. TaskHealth computes every progress-condition ONCE per turn from explicit
inputs (pure, no orchestrator instance) so all consumers read the same verdict.

This test pins the equivalence contract: TaskHealth's fields must match what the
existing scattered checks concluded, so it can replace them without behavior drift.
"""

from __future__ import annotations

from app.orchestration.task_health import compute_task_health, TaskHealth


# ── stagnation: range over the last window < min_delta ────────────────────────

def test_stagnant_when_flat_over_window():
    h = compute_task_health(
        self_progress_history=[40, 41, 40, 42, 41],
        capped_progress_history=[40, 41, 40, 42, 41],
        stagnant_window=5, min_delta=3,
    )
    assert h.stagnant is True


def test_not_stagnant_when_climbing():
    h = compute_task_health(
        self_progress_history=[10, 30, 55, 80],
        capped_progress_history=[10, 30, 55, 80],
        stagnant_window=5, min_delta=3,
    )
    assert h.stagnant is False


def test_stagnation_reads_self_progress_not_capped():
    """E-R5 contract preserved: a low-pinning cap must NOT fake stagnation when the
    self-graded estimate is still climbing."""
    h = compute_task_health(
        self_progress_history=[60, 80, 92],
        capped_progress_history=[10, 10, 10],   # cap pinned low (artifact-cap bug shape)
        stagnant_window=3, min_delta=3,
    )
    assert h.stagnant is False


# ── plateau: no new high-water-mark for N turns (catches oscillation) ──────────

def test_plateau_on_oscillation_that_defeats_range_check():
    # 88<->91 keeps range=3 (not < min_delta) but never makes a NEW peak.
    hist = [88, 91, 88, 91, 88, 91, 88, 91]
    h = compute_task_health(
        self_progress_history=hist, capped_progress_history=hist,
        plateau_limit=4,
    )
    assert h.plateaued is True


def test_not_plateaued_when_new_peaks_appear():
    hist = [10, 20, 30, 40, 50]
    h = compute_task_health(
        self_progress_history=hist, capped_progress_history=hist,
        plateau_limit=4,
    )
    assert h.plateaued is False


# ── barren / looping / blocked pass through from explicit signals ─────────────

def test_barren_from_consecutive_barren_turns():
    h = compute_task_health(
        self_progress_history=[10], capped_progress_history=[10],
        consecutive_barren_turns=4, barren_halt_at=4,
    )
    assert h.barren is True


def test_severity_and_progress_exposed():
    h = compute_task_health(
        self_progress_history=[13], capped_progress_history=[13],
        evolution_severity=0.8,
    )
    assert h.severity == 0.8
    assert h.current_progress == 13


# ── thin data never a false alarm ─────────────────────────────────────────────

def test_thin_history_not_stagnant_not_plateaued():
    h = compute_task_health(
        self_progress_history=[50], capped_progress_history=[50],
        stagnant_window=5, plateau_limit=4,
    )
    assert h.stagnant is False
    assert h.plateaued is False


def test_empty_history_safe():
    h = compute_task_health(self_progress_history=[], capped_progress_history=[])
    assert h.stagnant is False and h.plateaued is False and h.current_progress == 0


# ── summary string for prompt/logging ─────────────────────────────────────────

def test_summary_is_human_readable():
    h = compute_task_health(
        self_progress_history=[88, 91, 88, 91], capped_progress_history=[88, 91, 88, 91],
        plateau_limit=4, evolution_severity=0.9,
    )
    s = h.summary()
    assert isinstance(s, str) and s
