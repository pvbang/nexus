# Anti-Runaway-Loop (Group A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add control-loop guards that stop verification-theater runaways (the 163-turn `f188a4c4` loop stuck at 88–90%) without harming legitimately-long tasks.

**Architecture:** Four mechanisms layered onto the existing pure-policy + pipeline-loop split. Part 1 (plateau) is a pure predicate in `termination.py`. Part 2 (veto limit) wires a per-task counter into the anti-theater gates in `pipeline.py`. Part 3 (cross-turn semantic loop) extends the existing loop terminator in `nexus_orchestrator.py`. Part 4 (reconciliation one-shot + role lock) reuses the existing one-shot synthesis pattern and adds role canonicalization in `plan_next_turn`.

**Tech Stack:** Python 3.13, pytest. Pure functions in `termination.py` (no LLM/IO), stateful per-task dicts on the orchestrator instance.

## Global Constraints

- All thresholds are named module-level constants (project pattern: externalize magic numbers).
- Pure policy functions in `termination.py` take state as parameters — no `self`, no IO, no LLM.
- `nexus_orchestrator.py` re-exports every new `termination` constant in the existing `from app.orchestration.termination import (...)` block at `nexus_orchestrator.py:58` (`# noqa: F401  (re-exported)`), so existing import paths keep working.
- Hard ceilings always win over anti-theater gates (existing invariant: gates are skipped when `hard_ceiling_reached`). Plateau is a hard ceiling — it must set `hard_ceiling_reached` semantics so gates do not re-open it.
- Follow existing structlog style: `logger.warning("event_name", key=value, ...)`.
- Run the full existing suite before declaring done: `tests/test_orchestrator_completion.py`, `tests/test_orchestration_fixes.py`, `tests/test_orchestrator_termination_ladder.py`, `tests/test_orchestrator_termination_wiring.py`.

## Constants summary

| Constant | Value | Module | Meaning |
|---|---|---|---|
| `_MAX_PLATEAU_TURNS` | 20 | termination.py | non-security turns w/o a new progress peak → stop |
| `_MAX_PLATEAU_TURNS_SECURITY` | 30 | termination.py | security turns w/o a new peak → stop |
| `_PLATEAU_PEAK_EPSILON` | 1 | termination.py | a "new peak" must exceed prior HWM by > this (pct) |
| `_MAX_VETOES_PER_KIND` | 4 | termination.py | same veto kind limit before completion proceeds |
| `_SEMANTIC_LOOP_TERMINATE_AT` | 5 | termination.py | same (role, normalized-outcome) recurrences → loop stop |

---

## File Structure

- `app/orchestration/termination.py` — add `_MAX_PLATEAU_TURNS*`, `_PLATEAU_PEAK_EPSILON`, `_MAX_VETOES_PER_KIND`, `_SEMANTIC_LOOP_TERMINATE_AT`; add pure helpers `turns_since_new_peak()`, `plateau_reached()`, `veto_limit_reached()`, `semantic_loop_terminal()`; wire plateau into `evaluate_completion`.
- `app/orchestration/nexus_orchestrator.py` — re-export new constants; pass plateau into `_determine_completion` log; add `_veto_counts` + `_reconciliation_used_by_task` dicts; cross-turn semantic loop in the loop-terminator block (~1683); role canonicalization in `plan_next_turn`.
- `app/orchestration/pipeline.py` — increment veto counter when a gate flips `is_complete=False`; expose `hard_ceiling_reached` to also be set by plateau; trigger reconciliation one-shot.
- Tests: `tests/test_termination_plateau.py` (new), `tests/test_termination_veto_limit.py` (new), `tests/test_termination_semantic_loop.py` (new), `tests/test_orchestrator_role_lock.py` (new).

---

## Task 1: Plateau (high-water-mark) detection — pure helpers

**Files:**
- Modify: `app/orchestration/termination.py` (add constants after line 30; add helpers after `should_terminate_for_loop` ~line 194)
- Test: `tests/test_termination_plateau.py` (create)

**Interfaces:**
- Produces: `turns_since_new_peak(progress_history: list[int]) -> int`, `plateau_reached(progress_history: list[int], is_security_task: bool = False) -> bool`. Constants `_MAX_PLATEAU_TURNS=20`, `_MAX_PLATEAU_TURNS_SECURITY=30`, `_PLATEAU_PEAK_EPSILON=1`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_termination_plateau.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_termination_plateau.py -v`
Expected: FAIL with `ImportError: cannot import name 'plateau_reached'`

- [ ] **Step 3: Add constants**

In `app/orchestration/termination.py`, after line 30 (`_MIN_PROGRESS_DELTA = 3 ...`):

```python
# Plateau (high-water-mark) detection — audit f188a4c4 (Group A).
# The stagnation gate (range < _MIN_PROGRESS_DELTA over a window) is defeated by
# an oscillation that keeps variance >= 3% (e.g. 88<->91). Plateau is variance-
# independent: it asks whether progress has reached a NEW high-water-mark recently.
# A loop that never exceeds its peak is converged-or-stuck regardless of wobble.
_MAX_PLATEAU_TURNS = 20  # non-security: turns since the last new peak → stop.
_MAX_PLATEAU_TURNS_SECURITY = 30  # security/intel tasks get a longer runway.
_PLATEAU_PEAK_EPSILON = 1  # a "new peak" must exceed the prior HWM by > this (pct).
```

- [ ] **Step 4: Add the pure helpers**

In `app/orchestration/termination.py`, after `should_terminate_for_loop` (ends ~line 194):

```python
def turns_since_new_peak(progress_history: list[int]) -> int:
    """How many of the most-recent turns failed to set a NEW progress peak.

    A "new peak" is a value exceeding the running high-water-mark by more than
    ``_PLATEAU_PEAK_EPSILON``. Returns the count of trailing turns that did not
    establish such a peak. 0 when the latest turn set a new peak (still climbing).
    Variance below the peak does not reset the counter — that is the whole point.
    """
    if not progress_history:
        return 0
    hwm = progress_history[0]
    last_peak_idx = 0
    for i in range(1, len(progress_history)):
        if progress_history[i] > hwm + _PLATEAU_PEAK_EPSILON:
            hwm = progress_history[i]
            last_peak_idx = i
    return (len(progress_history) - 1) - last_peak_idx


def plateau_reached(
    progress_history: list[int],
    is_security_task: bool = False,
) -> bool:
    """Whether progress has plateaued: no new peak for the plateau window.

    Distinct from the stagnation gate — plateau cannot be defeated by an
    oscillation that keeps the progress RANGE >= _MIN_PROGRESS_DELTA, because it
    keys off reaching a NEW high-water-mark, not off variance.
    """
    limit = _MAX_PLATEAU_TURNS_SECURITY if is_security_task else _MAX_PLATEAU_TURNS
    # Need at least `limit` trailing non-peak turns AND enough history to judge.
    if len(progress_history) <= limit:
        return False
    return turns_since_new_peak(progress_history) >= limit
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_termination_plateau.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/termination.py tests/test_termination_plateau.py
git commit -m "feat(termination): plateau (high-water-mark) detection pure helpers

Variance-independent stop signal: counts trailing turns with no new progress
peak. Catches the 88<->91 oscillation that defeats the stagnation gate (range
< 3% never true). Origin: audit task f188a4c4.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Wire plateau into `evaluate_completion`

**Files:**
- Modify: `app/orchestration/termination.py` (`evaluate_completion`, after the stagnation block ~line 268)
- Test: `tests/test_termination_plateau.py` (extend)

**Interfaces:**
- Consumes: `plateau_reached` (Task 1). `evaluate_completion` already receives `progress_history` and `is_security_task`.
- Produces: `CompletionDecision(True, "plateau", {...})` when plateau and the LLM has not legitimately completed.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_termination_plateau.py
from app.orchestration.termination import evaluate_completion


def test_evaluate_completion_force_stops_on_plateau():
    history = [91] + [88, 90] * 10  # 20 turns since peak, non-security
    decision = evaluate_completion(
        llm_decision="continue",
        progress_pct=90,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_termination_plateau.py::test_evaluate_completion_force_stops_on_plateau -v`
Expected: FAIL (reason is `llm_decision`/`stagnation`, not `plateau`)

- [ ] **Step 3: Add the plateau gate**

In `evaluate_completion`, immediately AFTER the stagnation `if` block (after line 268, before the "If progress is climbing" comment at line 270):

```python
    # SAFETY CEILING 2b: plateau — no NEW progress peak for the plateau window.
    # Catches oscillations the stagnation range-check misses (88<->91 keeps range
    # >= 3 forever). Variance-independent: keys off reaching a new high-water-mark.
    if plateau_reached(progress_history, is_security_task=is_security_task):
        return CompletionDecision(
            True,
            "plateau",
            {
                "turns_since_new_peak": turns_since_new_peak(progress_history),
                "hwm": max(progress_history) if progress_history else 0,
                "current_pct": progress_pct,
            },
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_termination_plateau.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run regression suite**

Run: `pytest tests/test_orchestrator_completion.py tests/test_orchestration_fixes.py -v`
Expected: PASS (no existing test depends on a plateau-free path at >20 flat turns; if any fails, it is a genuine behavior overlap — record it before changing).

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/termination.py tests/test_termination_plateau.py
git commit -m "feat(termination): force-stop on plateau in evaluate_completion

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Re-export constants + log plateau reason in `_determine_completion`

**Files:**
- Modify: `app/orchestration/nexus_orchestrator.py` (import block line 58; `_determine_completion` reason-logging ~line 1296)
- Test: `tests/test_orchestrator_completion.py` (extend — uses the `orchestrator` fixture)

**Interfaces:**
- Consumes: `evaluate_completion` returns `reason="plateau"` (Task 2).
- Produces: `_determine_completion` returns `True` and logs `hard_ceiling_plateau` for a plateaued task.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_orchestrator_completion.py
def test_determine_completion_stops_on_plateau(orchestrator):
    tid = "t-plateau"
    history = [91] + [88, 90] * 10  # 20 turns since peak
    orchestrator._progress_history_by_task[tid] = history
    orchestrator._progress_by_task[tid] = 90
    assert orchestrator._determine_completion(
        llm_decision="continue",
        progress_pct=90,
        turn_count=163,
        task_id=tid,
    ) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_completion.py::test_determine_completion_stops_on_plateau -v`
Expected: FAIL (returns False — plateau reason not handled in logging path; but note `evaluate_completion` already returns True, so this may PASS for the return value. If it PASSES, keep it as a regression pin and proceed to add the log branch.)

- [ ] **Step 3: Add constant re-exports**

In `app/orchestration/nexus_orchestrator.py`, inside the `from app.orchestration.termination import (` block (line 58), add (keep alphabetical-ish with the existing entries):

```python
    _MAX_PLATEAU_TURNS,
    _MAX_PLATEAU_TURNS_SECURITY,
    _MAX_VETOES_PER_KIND,
    _PLATEAU_PEAK_EPSILON,
    _SEMANTIC_LOOP_TERMINATE_AT,
    plateau_reached,
    semantic_loop_terminal,
    turns_since_new_peak,
    veto_limit_reached,
```

(Note: `_MAX_VETOES_PER_KIND`, `_SEMANTIC_LOOP_TERMINATE_AT`, `semantic_loop_terminal`, `veto_limit_reached` are added in Tasks 4–5; importing them now is safe only after those constants/functions exist. If implementing strictly in order, add only the Task-1/2 names here and append the rest in Tasks 4–5.)

- [ ] **Step 4: Add the plateau log branch**

In `_determine_completion`, in the reason-dispatch block (after the `stagnation` branch at line 1298-1299):

```python
        elif decision.reason == "plateau":
            logger.warning("hard_ceiling_plateau", **decision.detail)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_completion.py::test_determine_completion_stops_on_plateau -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/nexus_orchestrator.py tests/test_orchestrator_completion.py
git commit -m "feat(orchestrator): wire + log plateau termination reason

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Per-kind veto limit — pure helper + counter

**Files:**
- Modify: `app/orchestration/termination.py` (constant + helper)
- Modify: `app/orchestration/nexus_orchestrator.py` (`__init__` ~line 311: add `_veto_counts` dict)
- Test: `tests/test_termination_veto_limit.py` (create)

**Interfaces:**
- Produces: `_MAX_VETOES_PER_KIND=4`; `veto_limit_reached(count: int) -> bool`. Orchestrator instance gains `self._veto_counts: dict[str, dict[str, int]]` (task_id → kind → count).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_termination_veto_limit.py
from __future__ import annotations

from app.orchestration.termination import _MAX_VETOES_PER_KIND, veto_limit_reached


def test_veto_limit_not_reached_below_threshold():
    assert veto_limit_reached(_MAX_VETOES_PER_KIND - 1) is False


def test_veto_limit_reached_at_threshold():
    assert veto_limit_reached(_MAX_VETOES_PER_KIND) is True
    assert veto_limit_reached(_MAX_VETOES_PER_KIND + 1) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_termination_veto_limit.py -v`
Expected: FAIL `ImportError: cannot import name 'veto_limit_reached'`

- [ ] **Step 3: Add constant + helper**

In `termination.py`, after the plateau constants:

```python
# Per-kind veto limit — audit f188a4c4 (Group A). Each anti-theater gate veto is
# counted by KIND (needs_review/tautology/reexec_mismatch/numeric_conflict/
# contradiction). When the SAME kind vetoes this many times without progress
# reaching a new peak, the gate is stuck in a self-reinforcing loop (inject critic
# → "new" finding → re-veto); allow completion through to the reconciliation turn.
_MAX_VETOES_PER_KIND = 4
```

After `plateau_reached`:

```python
def veto_limit_reached(count: int) -> bool:
    """Whether a single veto KIND has fired enough times to be a stuck loop."""
    return count >= _MAX_VETOES_PER_KIND
```

- [ ] **Step 4: Add the per-task counter dict**

In `nexus_orchestrator.py` `__init__`, near line 311 (after `_budget_synthesis_used_by_task`):

```python
        # task_id → {veto_kind → count}. Reset when progress sets a new HWM peak.
        self._veto_counts: dict[str, dict[str, int]] = {}
        # task_id → bool: granted the single reconciliation turn before force-stop.
        self._reconciliation_used_by_task: dict[str, bool] = {}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_termination_veto_limit.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/termination.py app/orchestration/nexus_orchestrator.py tests/test_termination_veto_limit.py
git commit -m "feat(termination): per-kind veto limit helper + counter dicts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Veto-counter wiring in the anti-theater gates

**Files:**
- Modify: `app/orchestration/pipeline.py` (`_postprocess_evaluation`, the five gate blocks 1722–1880)
- Test: `tests/test_termination_veto_limit.py` (extend with a gate-counting helper test)

**Interfaces:**
- Consumes: `self._veto_counts` (Task 4), `veto_limit_reached` (Task 4).
- Produces: a private method `_record_and_check_veto(task_id: str, kind: str) -> bool` on the pipeline class (returns True when the kind is STILL allowed to veto, False when its limit is reached and the veto must be suppressed). The pipeline reads `_veto_counts` off the orchestrator it holds (`self._orchestrator` — verify the attribute name in pipeline `__init__`; if the pipeline does not hold the orchestrator, store `_veto_counts` on the pipeline instead, keyed by task_id).

> **Implementer note:** Before writing, grep `app/orchestration/pipeline.py` for how it reaches orchestrator state (`self._orchestrator`, `orchestrator.`). The veto counter must live wherever the per-task progress already lives so the HWM-reset (Task 6) can clear it. Prefer storing `_veto_counts` on the SAME object as `_progress`. If `_postprocess_evaluation` cannot see the orchestrator, store the dict on the pipeline (`self._veto_counts`) and have Task 6 reset it via a method the pipeline calls.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_termination_veto_limit.py
def test_same_kind_veto_suppressed_at_limit():
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
```

- [ ] **Step 2: Run test to verify it fails / passes**

Run: `pytest tests/test_termination_veto_limit.py::test_same_kind_veto_suppressed_at_limit -v`
Expected: PASS (pure logic — this test pins the intended wiring semantics before editing pipeline).

- [ ] **Step 3: Add the counter helper to the pipeline class**

In `pipeline.py`, add a method on the same class as `_postprocess_evaluation`:

```python
    def _veto_allowed(self, kind: str) -> bool:
        """Count one veto of ``kind`` for this task and report if it may still veto.

        Returns True while the kind is below _MAX_VETOES_PER_KIND (the gate should
        veto as normal). Returns False once the kind has hit its limit — the gate
        must then NOT re-open completion (the loop is self-reinforcing; route to the
        reconciliation turn instead). Reset to empty when progress sets a new HWM
        peak (see Task 6).
        """
        from app.orchestration.termination import veto_limit_reached

        store = self._veto_counts.setdefault(self._task_id, {})
        store[kind] = store.get(kind, 0) + 1
        if veto_limit_reached(store[kind]):
            logger.warning(
                "veto_limit_reached", task_id=self._task_id[:8], kind=kind, count=store[kind],
            )
            return False
        return True
```

Add `self._veto_counts: dict[str, dict[str, int]] = {}` to the pipeline `__init__` (mirror of the orchestrator dict; Task 6 resets it).

- [ ] **Step 4: Guard each gate with the counter**

In `_postprocess_evaluation`, wrap each gate's veto. Example for A3-gate-2 (`needs_review`, line 1791): change

```python
            if not hard_ceiling_reached and completion_needs_independent_review(
                is_complete=evaluation.is_complete,
                self_attested_recent=_self_attested,
                independent_review_recent=independent_review_recent,
            ):
```

to

```python
            if (
                not hard_ceiling_reached
                and completion_needs_independent_review(
                    is_complete=evaluation.is_complete,
                    self_attested_recent=_self_attested,
                    independent_review_recent=independent_review_recent,
                )
                and self._veto_allowed("needs_review")
            ):
```

Apply the same `and self._veto_allowed("<kind>")` to: tautology gate (line 1758, kind `"tautology"`), reexec gate (line 1833, kind `"reexec_mismatch"`), numeric gate (line 1860, kind `"numeric_conflict"`). For the verification gate (line 1727, kind `"failed_verification"`) keep it UNCOUNTED — a workspace-contradicted "done" claim is ground-truth, not a self-reinforcing opinion loop; document this with a one-line comment.

- [ ] **Step 5: Run the suite**

Run: `pytest tests/test_termination_veto_limit.py tests/test_orchestration_fixes.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/pipeline.py tests/test_termination_veto_limit.py
git commit -m "feat(pipeline): cap same-kind anti-theater vetoes per task

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Reset veto counts on a new HWM peak

**Files:**
- Modify: `app/orchestration/nexus_orchestrator.py` (`_evaluate_turn`, at `self._progress.record(...)` line 1206)
- Modify: `app/orchestration/pipeline.py` if `_veto_counts` lives there (clear via a small method)
- Test: `tests/test_termination_veto_limit.py` (extend)

**Interfaces:**
- Consumes: `turns_since_new_peak` (Task 1), `self._veto_counts`.
- Produces: veto counts for a task cleared when the recorded progress exceeds the prior HWM by > `_PLATEAU_PEAK_EPSILON`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_termination_veto_limit.py
def test_new_peak_resets_veto_counts():
    # pure-logic mirror of the orchestrator reset rule
    history = [50, 60, 60, 60]
    counts = {"needs_review": 3}
    new_pct = 70  # exceeds hwm 60 by > epsilon → reset
    hwm = max(history)
    if new_pct > hwm + 1:  # _PLATEAU_PEAK_EPSILON
        counts = {}
    assert counts == {}
```

- [ ] **Step 2: Run test**

Run: `pytest tests/test_termination_veto_limit.py::test_new_peak_resets_veto_counts -v`
Expected: PASS (pins the reset rule).

- [ ] **Step 3: Implement the reset at the progress-record site**

In `nexus_orchestrator.py`, immediately before `self._progress.record(state.task_id, progress_pct)` (line 1206):

```python
            # Group A: a NEW high-water-mark is genuine progress — re-arm the
            # anti-theater gates by clearing this task's veto counts.
            _prior_hist = self._progress_history_by_task.get(state.task_id, [])
            _prior_hwm = max(_prior_hist) if _prior_hist else 0
            if progress_pct > _prior_hwm + _PLATEAU_PEAK_EPSILON:
                self._veto_counts.pop(state.task_id, None)
```

If `_veto_counts` lives on the pipeline (Task 5 implementer note), instead call a pipeline method `self._reset_vetoes(task_id)` from the equivalent location, or have the pipeline perform the reset where it already reads progress.

- [ ] **Step 4: Run the suite**

Run: `pytest tests/test_termination_veto_limit.py tests/test_orchestrator_completion.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/orchestration/nexus_orchestrator.py tests/test_termination_veto_limit.py
git commit -m "feat(orchestrator): reset veto counts on a new progress peak

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Cross-turn semantic loop detection

**Files:**
- Modify: `app/orchestration/termination.py` (constant + helper)
- Modify: `app/orchestration/nexus_orchestrator.py` (loop-terminator block ~1683-1705)
- Test: `tests/test_termination_semantic_loop.py` (create)

**Interfaces:**
- Produces: `_SEMANTIC_LOOP_TERMINATE_AT=5`; `semantic_loop_terminal(role_outcome_history: list[tuple[str, str]]) -> bool` — True when the most-recent `(role, normalized_outcome)` pair has occurred `>= _SEMANTIC_LOOP_TERMINATE_AT` times in the history.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_termination_semantic_loop.py
from __future__ import annotations

from app.orchestration.termination import (
    _SEMANTIC_LOOP_TERMINATE_AT,
    semantic_loop_terminal,
)


def test_repeated_role_outcome_is_terminal():
    hist = [("critic", "re-derived 421m N=32 pass")] * _SEMANTIC_LOOP_TERMINATE_AT
    assert semantic_loop_terminal(hist) is True


def test_varied_outcomes_not_terminal():
    hist = [("critic", f"finding {i}") for i in range(_SEMANTIC_LOOP_TERMINATE_AT)]
    assert semantic_loop_terminal(hist) is False


def test_same_outcome_different_roles_not_terminal():
    hist = [(f"role{i}", "same text") for i in range(_SEMANTIC_LOOP_TERMINATE_AT)]
    assert semantic_loop_terminal(hist) is False


def test_below_threshold_not_terminal():
    hist = [("critic", "x")] * (_SEMANTIC_LOOP_TERMINATE_AT - 1)
    assert semantic_loop_terminal(hist) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_termination_semantic_loop.py -v`
Expected: FAIL `ImportError: cannot import name 'semantic_loop_terminal'`

- [ ] **Step 3: Add constant + helper**

In `termination.py`:

```python
# Cross-turn semantic loop — audit f188a4c4 (Group A). The assignment-hash
# terminator keys on task_description[:200], which a critic varies each turn so
# the hash never repeats. This keys on the RESULT instead: when the same
# (role, normalized-outcome) recurs this many times, the team is re-producing the
# same finding under different task text — a loop the hash terminator misses.
_SEMANTIC_LOOP_TERMINATE_AT = 5


def semantic_loop_terminal(role_outcome_history: list[tuple[str, str]]) -> bool:
    """Whether the latest (role, outcome) pair has recurred to the loop ceiling.

    ``role_outcome_history`` is ordered oldest→newest; each entry is
    ``(agent_role, normalized_outcome_summary)``. Returns True when the most
    recent pair appears at least ``_SEMANTIC_LOOP_TERMINATE_AT`` times.
    """
    if len(role_outcome_history) < _SEMANTIC_LOOP_TERMINATE_AT:
        return False
    latest = role_outcome_history[-1]
    return role_outcome_history.count(latest) >= _SEMANTIC_LOOP_TERMINATE_AT
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_termination_semantic_loop.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire into the loop-terminator block**

In `nexus_orchestrator.py`, find the loop-terminator block (~1683-1705) that builds `turn_hash` and calls `should_terminate_for_loop`. Build a `(role, normalized_outcome)` history from completed turns and add an OR-branch. Add the import name `semantic_loop_terminal` (already added in Task 3's re-export block). Insert near the existing `should_terminate_for_loop` call (~1699):

```python
        # Cross-turn semantic loop: same (role, outcome) recurring under varied
        # task text (the hash terminator misses this). Build from completed turns.
        _ro_hist: list[tuple[str, str]] = []
        for _tr in state.turns_completed[-_SEMANTIC_LOOP_TERMINATE_AT * 2:]:
            for _ar in _tr.agent_results:
                _outcome = (getattr(_ar, "output", "") or "")[:120].strip().lower()
                _ro_hist.append(((_ar.agent_role or ""), _outcome))
        if semantic_loop_terminal(_ro_hist):
            logger.warning(
                "semantic_loop_terminated",
                task_id=state.task_id[:8],
                pair=_ro_hist[-1] if _ro_hist else None,
            )
            return None  # same terminal path as should_terminate_for_loop
```

> **Implementer note:** match the existing block's control flow — if `should_terminate_for_loop` there sets a flag / returns `None` / raises, mirror exactly that. Confirm `plan_next_turn` returning `None` is the terminal signal (it is: `pipeline.py:818-821`).

- [ ] **Step 6: Run the suite**

Run: `pytest tests/test_termination_semantic_loop.py tests/test_orchestrator_termination_ladder.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/orchestration/termination.py app/orchestration/nexus_orchestrator.py tests/test_termination_semantic_loop.py
git commit -m "feat(orchestrator): cross-turn semantic loop terminator

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Role lock — canonicalize ad-hoc roles to the declared team

**Files:**
- Modify: `app/orchestration/nexus_orchestrator.py` (`plan_next_turn`, before the role-rotation block ~1500)
- Test: `tests/test_orchestrator_role_lock.py` (create)

**Interfaces:**
- Produces: `canonicalize_role(role: str, team_roles: list[str]) -> str` (pure, add to `termination.py` or a small helper module). Maps an unknown role to the nearest declared team role by token overlap; returns the role unchanged if already in the team.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_role_lock.py
from __future__ import annotations

from app.orchestration.termination import canonicalize_role

TEAM = ["engineer", "data_scientist", "developer", "physicist", "writer", "critic"]


def test_known_role_unchanged():
    assert canonicalize_role("critic", TEAM) == "critic"


def test_writer_synthesis_variant_maps_to_writer():
    assert canonicalize_role("technical_writer / synthesis_agent", TEAM) == "writer"


def test_finalizer_maps_to_writer_when_no_better_match():
    # "finalizer / document-assembly" shares "document/assembly/writer" semantics
    out = canonicalize_role("finalizer / output-assembly agent", TEAM)
    assert out in TEAM


def test_radar_engineer_variant_maps_to_engineer():
    assert canonicalize_role("radar_systems_engineer (link-budget)", TEAM) == "engineer"


def test_empty_role_maps_to_first_team_role():
    assert canonicalize_role("", TEAM) == TEAM[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_role_lock.py -v`
Expected: FAIL `ImportError: cannot import name 'canonicalize_role'`

- [ ] **Step 3: Implement `canonicalize_role`**

In `termination.py`:

```python
# Role lock — audit f188a4c4 (Group A). 163 turns spawned ~90+ ad-hoc role names
# (mostly "finalizer / synthesis_agent" variants), abandoning the fixed team so
# knowledge never accumulated (the T1 Swerling finding was re-discovered at T160).
# Canonicalize any role to the nearest DECLARED team role by token overlap.
_ROLE_SYNONYMS = {
    "writer": ("writer", "synthesis", "synthesizer", "finalizer", "assembly",
               "assembler", "document", "editor", "report", "publish", "delivery"),
    "engineer": ("engineer", "radar", "rf", "sensor", "link-budget", "systems"),
    "critic": ("critic", "review", "reviewer", "qa", "audit", "verification",
               "refute", "judge"),
    "data_scientist": ("data_scientist", "estimation", "state-estimation", "kalman",
                        "filter", "statistician"),
    "developer": ("developer", "code", "simulation", "compute", "implementation"),
    "physicist": ("physicist", "electromagnetic", "propagation", "physics", "clutter"),
}


def canonicalize_role(role: str, team_roles: list[str]) -> str:
    """Map ``role`` to the nearest DECLARED team role (returns it unchanged if
    already declared). Falls back to token-overlap against ``_ROLE_SYNONYMS``;
    if nothing matches, returns the first team role."""
    if not team_roles:
        return role
    low = (role or "").lower()
    if role in team_roles:
        return role
    best_role, best_score = team_roles[0], 0
    for canon in team_roles:
        tokens = _ROLE_SYNONYMS.get(canon, (canon,))
        score = sum(1 for t in tokens if t in low)
        if score > best_score:
            best_role, best_score = canon, score
    return best_role
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_role_lock.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Apply in `plan_next_turn`**

In `nexus_orchestrator.py`, BEFORE the role-rotation block (line ~1500, after `assignments` is available), canonicalize every assignment's role against the declared team:

```python
        # Group A role lock: pin ad-hoc role names to the declared team so
        # knowledge accumulates per role instead of fragmenting across variants.
        if assignments and state.total_agents:
            for _a in assignments:
                _canon = canonicalize_role(_a.agent_role, list(state.total_agents))
                if _canon != _a.agent_role:
                    logger.info(
                        "role_canonicalized",
                        task_id=state.task_id[:8],
                        original=_a.agent_role, mapped=_canon,
                    )
                    _a.agent_role = _canon
```

Add `canonicalize_role` to the Task-3 re-export import block.

- [ ] **Step 6: Run the suite**

Run: `pytest tests/test_orchestrator_role_lock.py tests/test_orchestration_fixes.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/orchestration/termination.py app/orchestration/nexus_orchestrator.py tests/test_orchestrator_role_lock.py
git commit -m "feat(orchestrator): role lock — canonicalize ad-hoc roles to the team

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Reconciliation turn before force-stop

**Files:**
- Modify: `app/orchestration/nexus_orchestrator.py` (`_determine_completion` — grant one-shot before returning True for plateau)
- Test: `tests/test_orchestrator_completion.py` (extend)

**Interfaces:**
- Consumes: `self._reconciliation_used_by_task` (Task 4), plateau/semantic-loop force-stop signals.
- Produces: when a Group-A force-stop fires and the task has NOT yet had its reconciliation turn, `_determine_completion` returns False once (granting one turn with a hard reconciliation directive) and sets the flag; the next force-stop returns True.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_orchestrator_completion.py
def test_plateau_grants_one_reconciliation_turn_then_stops(orchestrator):
    tid = "t-reconcile"
    history = [91] + [88, 90] * 10
    orchestrator._progress_history_by_task[tid] = history
    orchestrator._progress_by_task[tid] = 90
    # First plateau hit → one reconciliation turn (False), flag set.
    first = orchestrator._determine_completion(
        llm_decision="continue", progress_pct=90, turn_count=163, task_id=tid,
    )
    assert first is False
    assert orchestrator._reconciliation_used_by_task.get(tid) is True
    # Second plateau hit → hard stop.
    second = orchestrator._determine_completion(
        llm_decision="continue", progress_pct=90, turn_count=164, task_id=tid,
    )
    assert second is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_completion.py::test_plateau_grants_one_reconciliation_turn_then_stops -v`
Expected: FAIL (first call returns True — no reconciliation grant yet).

- [ ] **Step 3: Implement the one-shot grant**

In `_determine_completion`, after computing `decision = evaluate_completion(...)` (line 1287-1295) and BEFORE the reason-logging block, intercept the Group-A force-stop reasons:

```python
        # Group A: a plateau/semantic-loop force-stop gets exactly ONE honest
        # reconciliation turn (pin numbers to on-disk script output, report the
        # REAL %, list unresolved contradictions) before the hard stop — mirrors
        # the token-budget synthesis one-shot above.
        if decision.complete and decision.reason == "plateau":
            if not self._reconciliation_used_by_task.get(task_id, False):
                self._reconciliation_used_by_task[task_id] = True
                logger.warning(
                    "plateau_reconciliation_turn_granted",
                    task_id=task_id[:8], **decision.detail,
                )
                return False  # one more turn — the reconciliation pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_completion.py::test_plateau_grants_one_reconciliation_turn_then_stops -v`
Expected: PASS

- [ ] **Step 5: Inject the reconciliation directive into the granted turn**

In `plan_next_turn`, when `self._reconciliation_used_by_task.get(state.task_id)` is True AND this is the granted turn (progress plateaued), prepend a hard directive to `evaluation.next_turn_focus`. Add near the top of `plan_next_turn` after the early `is_complete` return:

```python
        if self._reconciliation_used_by_task.get(state.task_id) and not getattr(
            state, "_reconciliation_directive_emitted", False
        ):
            state._reconciliation_directive_emitted = True
            _focus = evaluation.next_turn_focus or ""
            evaluation.next_turn_focus = (
                "[RECONCILIATION — FINAL TURN] The loop has plateaued. Pin EVERY headline "
                "number to the actual stdout of the on-disk script that produces it (run it). "
                "Report the REAL progress %, list every unresolved contradiction explicitly, "
                "and do NOT fake completion. After this turn the task stops. " + _focus
            )[:500]
```

> **Implementer note:** if `ExecutionState` is a slots/dataclass that rejects ad-hoc attributes, store the one-shot flag in a dict on the orchestrator (`self._reconciliation_directive_by_task`) instead of on `state`.

- [ ] **Step 6: Run the suite**

Run: `pytest tests/test_orchestrator_completion.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/orchestration/nexus_orchestrator.py tests/test_orchestrator_completion.py
git commit -m "feat(orchestrator): one honest reconciliation turn before plateau stop

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Full regression + simulation of the f188a4c4 trajectory

**Files:**
- Test: `tests/test_termination_plateau.py` (add an end-to-end trajectory test)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the trajectory regression test**

```python
# append to tests/test_termination_plateau.py
def test_f188a4c4_trajectory_would_have_stopped():
    """Replays the real shape: climb to ~90 by turn ~30, then oscillate 88<->91.
    The pre-fix system ran 163 turns; plateau must fire well before that."""
    climb = list(range(0, 91, 5))          # turns 0..18 reach 90
    oscillate = [88, 90, 91, 89] * 40       # the 130+ wasted turns
    history = climb + oscillate
    # find first index where plateau_reached becomes True
    from app.orchestration.termination import plateau_reached
    first_stop = next(
        (i for i in range(len(history)) if plateau_reached(history[:i + 1])),
        None,
    )
    assert first_stop is not None
    # must stop within ~20 turns of the last genuine peak (91), not at 163
    assert first_stop < len(climb) + 25
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_termination_plateau.py::test_f188a4c4_trajectory_would_have_stopped -v`
Expected: PASS

- [ ] **Step 3: Full suite**

Run: `pytest tests/test_termination_plateau.py tests/test_termination_veto_limit.py tests/test_termination_semantic_loop.py tests/test_orchestrator_role_lock.py tests/test_orchestrator_completion.py tests/test_orchestration_fixes.py tests/test_orchestrator_termination_ladder.py tests/test_orchestrator_termination_wiring.py -v`
Expected: PASS (all). Record any failure and treat as a behavior conflict to investigate before merge.

- [ ] **Step 4: Commit**

```bash
git add tests/test_termination_plateau.py
git commit -m "test(termination): regression — f188a4c4 trajectory stops at plateau

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review notes

- **Spec coverage:** Part 1 → Tasks 1–3; Part 2 → Tasks 4–6; Part 3 → Task 7; Part 4a (reconciliation) → Task 9; Part 4b (role lock) → Task 8. Trajectory regression → Task 10.
- **Type consistency:** `plateau_reached`, `turns_since_new_peak`, `veto_limit_reached`, `semantic_loop_terminal`, `canonicalize_role` defined in Task 1/4/7/8 and consumed with identical signatures in Tasks 2/3/5/6/9. Constants named identically across the import block and helpers.
- **Known implementer decisions (flagged inline, not placeholders):** (a) where `_veto_counts` lives (orchestrator vs pipeline) — Task 5 note; (b) whether `ExecutionState` accepts ad-hoc attributes — Task 9 note. Both have a concrete fallback specified.
