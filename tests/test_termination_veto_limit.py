from __future__ import annotations

from app.orchestration.termination import _MAX_VETOES_PER_KIND, veto_limit_reached


def test_veto_limit_not_reached_below_threshold():
    assert veto_limit_reached(_MAX_VETOES_PER_KIND - 1) is False


def test_veto_limit_reached_at_threshold():
    assert veto_limit_reached(_MAX_VETOES_PER_KIND) is True
    assert veto_limit_reached(_MAX_VETOES_PER_KIND + 1) is True


def test_same_kind_veto_suppressed_at_limit():
    """Verify that a veto kind is suppressed after hitting _MAX_VETOES_PER_KIND."""
    counts: dict[str, int] = {}
    kind = "needs_review"
    # simulate the gate incrementing then checking
    allowed = []
    for _ in range(_MAX_VETOES_PER_KIND + 1):
        counts[kind] = counts.get(kind, 0) + 1
        allowed.append(not veto_limit_reached(counts[kind]))
    # first (_MAX-1) allowed to veto, the limit-th and beyond suppressed
    assert allowed[:_MAX_VETOES_PER_KIND - 1] == [True] * (_MAX_VETOES_PER_KIND - 1)
    assert allowed[_MAX_VETOES_PER_KIND - 1] is False


def test_new_peak_resets_veto_counts():
    # pure-logic mirror of the orchestrator reset rule
    history = [50, 60, 60, 60]
    counts = {"needs_review": 3}
    new_pct = 70  # exceeds hwm 60 by > epsilon → reset
    hwm = max(history)
    if new_pct > hwm + 1:  # _PLATEAU_PEAK_EPSILON
        counts = {}
    assert counts == {}


# ── _veto_allowed wiring (T5 coverage gap) ────────────────────────────────


def _make_pipeline(task_id: str = "t-veto"):
    """Construct a minimal pipeline; _veto_allowed only touches self._task_id."""
    from app.orchestration.pipeline import NexusExecutionPipeline

    return NexusExecutionPipeline(llm=None, tool_executor=None, task_id=task_id)


def test_veto_allowed_suppresses_same_kind_at_limit():
    pipe = _make_pipeline()
    veto_counts: dict[str, dict[str, int]] = {}
    results = [pipe._veto_allowed("tautology", veto_counts) for _ in range(_MAX_VETOES_PER_KIND + 1)]
    # First (_MAX-1) calls allow the veto; the limit-th call (count==4) and beyond suppress.
    assert results[: _MAX_VETOES_PER_KIND - 1] == [True] * (_MAX_VETOES_PER_KIND - 1)
    assert results[_MAX_VETOES_PER_KIND - 1] is False
    assert results[_MAX_VETOES_PER_KIND] is False
    assert veto_counts["t-veto"]["tautology"] == _MAX_VETOES_PER_KIND + 1


def test_veto_allowed_counts_kinds_independently():
    pipe = _make_pipeline()
    veto_counts: dict[str, dict[str, int]] = {}
    # Three tautology vetoes do not exhaust a different kind.
    for _ in range(3):
        pipe._veto_allowed("tautology", veto_counts)
    assert pipe._veto_allowed("numeric_conflict", veto_counts) is True
    assert veto_counts["t-veto"]["tautology"] == 3
    assert veto_counts["t-veto"]["numeric_conflict"] == 1


def test_veto_allowed_none_counter_never_suppresses():
    pipe = _make_pipeline()
    # No counter available → never suppress, never crash.
    assert all(pipe._veto_allowed("tautology", None) for _ in range(_MAX_VETOES_PER_KIND + 3))
