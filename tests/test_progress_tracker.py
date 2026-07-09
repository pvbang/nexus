"""Tests for ProgressTracker — the consolidated per-task progress store.

Pins the behaviour that previously lived as loose dicts on the orchestrator
(audit C1/C2): a single write path that keeps current % and history in sync, and
durability round-trips (including the JSON-string-key hazard that arises when an
ExecutionState.progress_history is restored from a checkpoint).
"""

from __future__ import annotations

from app.orchestration.progress_tracker import ProgressTracker


# ───────────────────────── single write path ─────────────────────────


def test_record_sets_current_and_appends_history_together():
    pt = ProgressTracker()
    pt.record("t1", 10)
    pt.record("t1", 25)
    assert pt.current("t1") == 25
    assert pt.history("t1") == [10, 25]


def test_reads_default_when_unknown_task():
    pt = ProgressTracker()
    assert pt.current("nope") == 0
    assert pt.history("nope") == []


def test_tasks_are_isolated():
    pt = ProgressTracker()
    pt.record("a", 10)
    pt.record("b", 90)
    assert pt.current("a") == 10
    assert pt.current("b") == 90
    assert pt.history("a") == [10]


# ───────────────────────── durability round-trip ─────────────────────────


def test_to_dict_then_load_round_trips():
    pt = ProgressTracker()
    pt.record("t1", 30)
    pt.record("t1", 55)
    snap = pt.to_dict("t1")

    restored = ProgressTracker()
    restored.load("t1", snap)
    assert restored.current("t1") == 55
    assert restored.history("t1") == [30, 55]


def test_to_dict_returns_independent_copies():
    pt = ProgressTracker()
    pt.record("t1", 30)
    snap = pt.to_dict("t1")
    snap["history"].append(999)  # mutating the snapshot must not corrupt tracker
    assert pt.history("t1") == [30]


def test_load_ignores_empty():
    pt = ProgressTracker()
    pt.record("t1", 30)
    pt.load("t1", None)
    pt.load("t1", {})
    assert pt.current("t1") == 30  # unchanged


# ───────────────────────── hydrate_from_turn_map ─────────────────────────


def test_hydrate_from_turn_map_rebuilds_in_turn_order():
    pt = ProgressTracker()
    pt.hydrate_from_turn_map("t1", {1: 10, 2: 30, 3: 60})
    assert pt.history("t1") == [10, 30, 60]
    assert pt.current("t1") == 60


def test_hydrate_handles_json_string_keys_in_numeric_order():
    """A checkpoint round-trip turns int turn keys into strings.

    Ordering must be numeric (1,2,...,10,11) not lexicographic ("1","10","11","2"),
    otherwise the history tail — and thus 'current' — would be wrong.
    """
    pt = ProgressTracker()
    turn_map = {str(i): i * 5 for i in range(1, 12)}  # "1".."11" → 5..55
    pt.hydrate_from_turn_map("t1", turn_map)
    assert pt.history("t1") == [i * 5 for i in range(1, 12)]
    assert pt.current("t1") == 55  # turn 11, not turn "9" (lexicographic last)


def test_hydrate_ignores_empty():
    pt = ProgressTracker()
    pt.hydrate_from_turn_map("t1", None)
    pt.hydrate_from_turn_map("t1", {})
    assert pt.current("t1") == 0
    assert pt.history("t1") == []


def test_hydrate_skips_corrupt_keys_without_crashing():
    # A corrupt/legacy checkpoint with a non-numeric turn key must NOT crash the
    # resume path — bad entries are skipped, good ones kept in numeric order.
    pt = ProgressTracker()
    pt.hydrate_from_turn_map("t1", {"1": 10, "bad": 99, "2": 30, "3": None})
    assert pt.history("t1") == [10, 30]  # "bad" and None-pct dropped
    assert pt.current("t1") == 30


def test_hydrate_all_corrupt_is_noop():
    pt = ProgressTracker()
    pt.hydrate_from_turn_map("t1", {"x": "y", "z": None})
    assert pt.current("t1") == 0
    assert pt.history("t1") == []
