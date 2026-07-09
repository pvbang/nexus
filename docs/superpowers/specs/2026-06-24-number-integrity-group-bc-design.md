# Number-Integrity (Group B/C) — Design Spec

**Date:** 2026-06-24
**Branch:** `fix/group-b-c-number-integrity`
**Origin:** Forensic audit of task `f188a4c4` (`workspace_data/tasks/f188a4c4/AUDIT_REPORT.md`, NHÓM C).

## Problem

The deliverable's headline radar number (`R_max(N=1)=338.50 m`, design `N≥85`) matched
NO on-disk script (the script prints `421.26 m`, `N=32 PASS`) — a reverse-fit number
labeled "code-verified". Two number-integrity safeguards exist but BOTH let it through:

| Gap | Location | Effect |
|---|---|---|
| **G1 — unit allowlist omits `m` (meters)** | `artifact_reexecutor.py:56-59` (`_HEADLINE_UNIT`); `numeric_integrity.py:49` (`_UNIT`) | Every radar headline is in meters → ZERO headline numbers extracted → both safeguards see nothing to check. The single most important number type for this task is invisible. |
| **G2 — re-exec diffs per-turn agent output, not the deliverable** | `pipeline.py:656` (`_claimed = [ar.output ...]`); `pipeline.py:664` (`run_numeric_integrity(_wd, report_text="")`) | The 338.50 headline lives in `FINAL_DELIVERABLE.md`, never in the "claimed" side, so it can never be contradicted. `report_text=""` makes CC-2 grounding a no-op. |

(Audit gaps G4 tautology-scope and G5 independent-review-semantics are orchestration-level
and OUT OF SCOPE for this spec — see Non-goals.)

## Goal

Make the number-integrity safeguards actually see (a) the SI units this domain uses, and
(b) the final deliverable text — so a deliverable headline number that no on-disk script
produces is caught as a mismatch / `[UNSOURCED]`, without introducing false positives.

## Design

### Part 1 — Extend the unit allowlists (G1), false-positive-safe

Add the SI units this and similar engineering domains use to BOTH regexes:
`artifact_reexecutor._HEADLINE_UNIT` and `numeric_integrity._UNIT`.

Units to add: `m`, `km`, `cm`, `mm` (already in numeric), `m/s`, `m/s²`/`m/s2`, `dB`, `dBi`,
`Hz`, `kHz`, `MHz`, `GHz`, `ms`, `µs`/`us`, `s`, `W`, `kW`, `dBm`.

**Critical false-positive guard (the load-bearing design decision):** bare `m`/`s` match
inside ordinary words ("3 models" → "3 m", "5 systems" → "5 s"). Two rules prevent this:
1. **Word-boundary anchor** — every ambiguous short unit is anchored with `\b` (e.g. `m\b`,
   `s\b`), so `m` matches `421 m.` / `421 m ` but NOT the `m` in `models` (m followed by
   `o`, both word chars → no boundary).
2. **Longest-alternative-first ordering** — alternation is ordered so multi-char units win:
   `m/s²|m/s|km|cm|mm|ms|...|m\b`. Regex alternation is first-match, so `m/s` and `mm` are
   tried before `m\b`. Otherwise `150 mm` would match `150 m` + stray `m`.

`dB`, `dBi`, `dBm`, `Hz`, `kHz`, `MHz`, `GHz` are distinctive (no word-collision) — added
plainly. `°C` etc. already present stay.

### Part 2 — Feed the deliverable into numeric-integrity (G2)

**Design correction (verified empirically before coding — do NOT feed the deliverable to
re-exec):** The audit suggested adding the deliverable to re-exec's `_claimed`. Testing
showed this is WRONG and would be worse than the bug: re-exec runs ONE script and flags a
`mismatch` if ANY claimed number is absent from that script's stdout. A real deliverable
holds many numbers — input parameters (10 W, 9.5 GHz), values derived by OTHER scripts
(NEES, gate χ², dwell), and figures from other sections — so feeding it to re-exec flagged
**12 of 15 numbers as mismatches** on a legitimate deliverable, which would block every
honest task. Re-exec's per-claim "absent ⇒ mismatch" rule is only sound when `_claimed` is
the output of the SAME agent that just ran THAT script — a 1:1 relationship the deliverable
does not have. **Re-exec is therefore left unchanged.**

The correct tool for "a deliverable number that no artifact produced" is **CC-2 grounding**
(`ground_report_numbers`), which is designed for exactly this: it compares each report
figure against ALL structured facts (every `.csv`/`.json` value in the workspace, not one
script's stdout) and ANNOTATES `[UNSOURCED]` rather than blocking. Verified: with the Part-1
units, a fabricated `338.50 m` / `986.46 m` gets `[UNSOURCED]` while the real `421.26 m`,
`10 W`, and `11.34` (all present in artifacts) are left untouched. No false positives.

In `pipeline.py` (~line 642-665, the `if evaluation.is_complete:` scan block):
1. Read the final deliverable text once. Resolve its path via the existing report mechanism
   (the `_work/report/*.md` the finalizer writes). Read it defensively (missing → empty
   string, never crash).
2. **Numeric integrity (CC-2/CC-3):** pass `report_text=deliverable_text` to
   `run_numeric_integrity(_wd, report_text=deliverable_text)` (was `""`), so CC-3
   reconciliation sees the headline prose. (`run_numeric_integrity` also already globs
   `_work/**/*.md`, but the canonical deliverable lives under `_work/report/` and is read
   explicitly so a relocated/renamed deliverable is still covered.)
3. **CC-2 grounding** already runs in `final_report._ground_numbers` at report-assembly
   time; with Part-1 units it now annotates unsourced meter/dB/Hz figures. No new wiring
   needed there — Part 1 activates it for this domain.

Re-exec's `_claimed` stays `[ar.output ...]` (per-turn agent output) — unchanged.
This keeps the existing fail-open behavior (no deliverable → empty string → no new blocks).

### Part 3 — (G3, partial) stdout-only scripts

The re-exec path (Part 2) already runs scripts and compares their stdout, so a stdout-only
`print()` script (which defeats `numeric_integrity`'s structured-artifact assumption) is
now covered for the DELIVERABLE's headline numbers via Part 2. No separate change needed;
documented here so the audit's G3 is explicitly addressed by the Part-2 wiring.

## Testing (TDD)

- `tests/test_number_units.py` (new):
  - `extract_headline_numbers("R_max = 421.26 m")` → `{421.26}` (was `set()`).
  - meters/`m/s`/`dB`/`MHz`/`Hz`/`km` all extracted with value.
  - **false-positive guards**: `"3 models"` → no `3`; `"5 systems"` → no `5`;
    `"150 mm"` → `{150}` once (not `150` + stray), `"a 2026 milestone"` → no spurious.
  - `numeric_integrity.extract_prose_facts("R_max=338.50 m")` → value `338.5`, unit `m`.
- `tests/test_number_grounding.py` (new): `ground_report_numbers` flags a fabricated
  deliverable headline (`338.50 m`, `986.46 m`) as `[UNSOURCED]` while leaving real
  artifact-backed figures (`421.26 m`, `10 W`) untouched — proving the f188a4c4 number
  would now be caught, with no false positive on legitimate inputs.
- Regression: `tests/test_orchestration_fixes.py` and any existing numeric/reexec tests
  stay green (the unit additions must not break existing % / kg / USD extraction).

## Files touched

- `app/orchestration/artifact_reexecutor.py` — extend `_HEADLINE_UNIT`.
- `app/orchestration/numeric_integrity.py` — extend `_UNIT`.
- `app/orchestration/pipeline.py` — read deliverable, feed into both scans.
- New tests as above.

## Non-goals

- G4 (tautology detector reads `.py` only / back-solve delegated to LLM critic) — design
  decision, separate effort.
- G5 (independent-review gate verifies a reviewer RAN, not its CONCLUSION) — orchestration
  semantics, separate effort.
- Re-running/repairing task `f188a4c4`.

## Risk note

The `m`/`s` unit additions are the only real risk (false positives). The word-boundary +
longest-first ordering is the mitigation; the test suite pins the false-positive cases
explicitly. If a unit proves too noisy in practice, it can be removed from one regex without
touching the wiring.
