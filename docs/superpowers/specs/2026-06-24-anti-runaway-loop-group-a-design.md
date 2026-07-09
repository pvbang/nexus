# Anti-Runaway-Loop (Group A) — Design Spec

**Date:** 2026-06-24
**Branch:** `fix/anti-runaway-loop-group-a`
**Origin:** Forensic audit of task `f188a4c4` (drone detection design, 163 turns stuck at 88–90%).
Full audit: `workspace_data/tasks/f188a4c4/AUDIT_REPORT.md`.

## Problem

Task `f188a4c4` ran **163 turns** without honest termination — a verification-theater
runaway. The deliverable's headline radar number (`R_max(N=1)=338.50 m`, design `N≥85`)
matches **no script on disk** (the on-disk `radar_budget_taskspec.py` prints `421.26 m`,
`N=32 PASS`); it was narrated by a critic and reverse-fit into prose. Meanwhile the loop
kept re-deriving the same number while progress oscillated 88↔91%.

Root cause is in the **control loop**, not the team's competence (the team correctly
computed Swerling/radar/observability at Turn 1). Four orchestration mechanisms all share
one blind spot — **turn-level oscillation with non-zero variance** — and one set of
guards actively feeds the loop:

| Mechanism | Location | Why it failed at 163 turns |
|---|---|---|
| Stagnation window | `termination.py:255-259` | fires only at `progress_range < 3%` / 15 turns; an 88↔91% wobble keeps range ≥ 3 → never fires |
| Max-turns ceiling | `termination.py:26,250` | =500, "not a task limit"; 163 ≪ 500 |
| Token cumulative ceiling | `token_governor.py:174-182` | absolute ~1.9M-token *cost* guard; a cheap re-derivation loop never reaches it; no progress-awareness |
| In-run stuck/loop detectors | `stuck_detector.py`; reset `agent_worker.py:492` | scoped to one agent react-loop, reset every turn → zero cross-turn memory |
| Assignment-hash terminator | `nexus_orchestrator.py:1685-1726` | hash = `md5(role:task_description[:200])`; critic's task text changes each turn → never repeats → never counts to 5 |
| Anti-theater gates + critic rotation | `pipeline.py:1727-1822`, contradiction block `termination.py:197-211` | **actively re-opens the task**: veto completion → inject critic → critic surfaces a "new" finding → re-arm block → bump progress enough to dodge stagnation |

Secondary role symptom: **role explosion** — 163 turns spawned ~90+ ad-hoc role names
(mostly `finalizer / synthesis_agent` variants). The fixed 6-role team was abandoned after
~turn 13 (`physicist` ran once at T1, `data_scientist` once at T13), so the Swerling
finding from T1 was "re-discovered" at T160 — institutional amnesia.

## Goal

Add control-loop guards that **cannot be defeated by non-zero-variance oscillation**, cap
the number of times completion can be vetoed, give the loop cross-turn loop memory, and
keep the team to its declared roles — then on force-stop, run exactly one honest
reconciliation turn instead of faking completion.

## Design

Four mechanisms. Thresholds are externalized as named constants (project pattern).

### Part 1 — Plateau detection (high-water-mark) · `termination.py`

Add a signal **independent of variance**. Track the high-water-mark (highest progress ever
reached). If progress fails to set a **new** peak above the HWM for `_MAX_PLATEAU_TURNS`
consecutive turns, it is a plateau → force honest-stop, regardless of how it wobbles below
the peak.

```
hwm = max(progress_history)
turns_since_new_peak = (# turns since progress last equalled hwm)
if turns_since_new_peak >= plateau_limit:   # 20 non-security / 30 security
    return CompletionDecision(True, "plateau", {...})
```

- Constants: `_MAX_PLATEAU_TURNS = 20`, `_MAX_PLATEAU_TURNS_SECURITY = 30`.
- Distinction from the existing stagnation gate: stagnation asks "is it wobbling?"
  (defeatable); plateau asks "is it reaching a *new* peak?" (not defeatable by wobble). The
  88↔91 loop never exceeds 91 → plateau fires.
- Pure function, added inside `evaluate_completion`, unit-tested. Existing stagnation gate
  is **kept** (it catches genuinely-flat tasks faster at 15 turns).

### Part 2 — Veto-completion limit · `pipeline.py` + `termination.py`

A per-task counter keyed by **veto kind** (`needs_review` / `tautology` /
`reexec_mismatch` / `numeric_conflict` / `contradiction`):

- Each time a gate flips `is_complete=False`, increment `_veto_counts[task_id][kind]`.
- When the **same kind** reaches `_MAX_VETOES_PER_KIND = 4`, stop vetoing that kind, log
  `veto_limit_reached`, and let completion proceed (routes into the Part-4 reconciliation
  turn).
- When progress sets a **new HWM peak**, reset all veto counts for the task (real progress
  re-earns the gates' right to veto).

Keyed by *kind*, not total: a legitimate task may be vetoed twice for tautology then twice
for numeric — that is real progress. Only the **same kind 4×** signals a self-reinforcing
loop.

### Part 3 — Cross-turn semantic loop memory · `nexus_orchestrator.py`

The assignment-hash terminator keys on `task_description[:200]`, which the critic varies
each turn. Add a **semantic** loop signal: hash `(role, normalized_outcome_summary)` —
i.e. the *result* repeating, not the *task text*. When the same `(role, outcome)` recurs
for `_SEMANTIC_LOOP_TERMINATE_AT = 5` turns (even with differing task text), treat it as a
loop → same force-stop path. This catches "80 turns re-deriving one number" that the raw
hash misses. Source of truth: the existing `ACTION_REGISTRY.jsonl` (`action_key`,
`attempt_count`) plus per-turn `outcome_summary`.

### Part 4 — Reconciliation turn + role lock

**4a — Reconciliation turn.** When any Part 1–3 mechanism triggers force-stop, do not stop
immediately: grant exactly **one** final turn (reuse the existing
`_budget_synthesis_used_by_task` one-shot pattern at `nexus_orchestrator.py:1272`). This
turn carries a hard directive: *"pin every number to on-disk script output, report the
REAL progress %, list unresolved contradictions, do NOT fake complete."* After that turn →
hard stop.

**4b — Role lock.** Add validation in `plan_next_turn`: each `agent_role` must belong to
the fixed team declared in `TASK_BRIEF` (engineer / data_scientist / developer / physicist
/ writer / critic). An unknown role is mapped to the nearest declared role and logged as
`role_canonicalized`, so knowledge accumulates per role instead of fragmenting across 90+
ad-hoc names.

## Testing (TDD — one test per mechanism)

- `test_termination.py`:
  - plateau fires at 20 turns with no new peak;
  - plateau does **not** fire while progress still climbs to new peaks;
  - oscillation 88↔91 fires plateau (regression for `f188a4c4`);
  - security task uses 30-turn plateau window.
- veto-limit: same kind 4× → completion proceeds; a new HWM peak resets counts.
- cross-turn loop: same `(role, outcome)` ×5 → force-stop.
- reconciliation: exactly one final turn runs, then `is_complete=True`.
- role lock: unknown role canonicalized to a declared role.
- regression: `tests/test_orchestrator_completion.py` + `tests/test_orchestration_fixes.py`
  still pass (existing guards intact).

## Files touched

- `app/orchestration/termination.py` — plateau predicate + constants; veto-limit constant.
- `app/orchestration/nexus_orchestrator.py` — HWM record, cross-turn semantic loop,
  reconciliation one-shot, role canonicalization in `plan_next_turn`.
- `app/orchestration/pipeline.py` — veto counter wiring at the anti-theater gates.

## Non-goals (explicitly out of scope)

- Group B (unit-allowlist / re-exec-diff-deliverable fixes) — separate spec.
- Group C (numeric_integrity / artifact_reexecutor internals).
- Re-running or repairing task `f188a4c4` itself.

## Constants summary

| Constant | Value | Meaning |
|---|---|---|
| `_MAX_PLATEAU_TURNS` | 20 | non-security turns w/o new peak → stop |
| `_MAX_PLATEAU_TURNS_SECURITY` | 30 | security/intel turns w/o new peak → stop |
| `_MAX_VETOES_PER_KIND` | 4 | same veto kind limit before completion proceeds |
| `_SEMANTIC_LOOP_TERMINATE_AT` | 5 | same (role,outcome) recurrences → loop stop |
