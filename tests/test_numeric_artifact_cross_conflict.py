"""CC-3b: cross-ARTIFACT numeric conflict (NII, audit tri-model 2026-07-01).

`check_reconciliation` compared prose against artifacts but never artifact against
artifact. Run 3a75a0db shipped THREE structured JSONs disagreeing on the SAME
quantities — equilibrium_results.json LHV≈7.29, mass_energy_balance.json LHV≈7.36,
design_basis_corrected.json LHV=5.30; operating point 1040 vs 1050 vs 850 °C — and
run 68c276ac had three reactor diameters (Ø496/550/534). The report happened to
match ONE of them, so the prose-vs-artifact gate stayed silent while the design
basis was internally contradictory. "The script ran" ≠ "the numbers agree."

`check_artifact_conflicts(artifact_facts)` flags the same labelled quantity
carrying ≥2 divergent values across DIFFERENT source files. It must NOT fire on:
  - a time-series (many values, one label, within ONE file/column), or
  - the same value repeated across many files (agreement).
Structured-vs-structured only; prose is handled by the existing check.
"""

from __future__ import annotations

import json

from app.orchestration import numeric_integrity as ni


def test_two_json_files_disagree_on_same_quantity_is_conflict(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "equilibrium_results.json").write_text(
        json.dumps({"lhv_mj_nm3": 7.29}), encoding="utf-8")
    (work / "mass_energy_balance.json").write_text(
        json.dumps({"lhv_mj_nm3": 5.30}), encoding="utf-8")
    rep = ni.run_numeric_integrity(work, report_text="")
    assert ni.count_conflicts(rep) >= 1, "two artifacts disagreeing on lhv must conflict"


def test_three_way_diameter_disagreement_is_conflict(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "specs.json").write_text(json.dumps({"reactor_diameter_mm": 496.0}), encoding="utf-8")
    (work / "report_calc.json").write_text(json.dumps({"reactor_diameter_mm": 550.0}), encoding="utf-8")
    rep = ni.run_numeric_integrity(work, report_text="")
    assert ni.count_conflicts(rep) >= 1


def test_same_value_across_files_no_conflict(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "a.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    (work / "b.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    rep = ni.run_numeric_integrity(work, report_text="")
    assert ni.count_conflicts(rep) == 0


def test_timeseries_in_one_csv_no_cross_conflict(tmp_path):
    # Many values, one label, ONE file → a legitimate series, never a conflict.
    import csv as _csv
    work = tmp_path / "_work"
    work.mkdir()
    with open(work / "revenue.csv", "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["revenue"])
        for v in (100, 200, 300):
            w.writerow([v])
    rep = ni.run_numeric_integrity(work, report_text="")
    assert ni.count_conflicts(rep) == 0


def test_different_units_same_label_across_files_not_conflict(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    # Same label token, different units → different quantities, not a conflict.
    (work / "a.json").write_text(json.dumps({"yield_pct": 28}), encoding="utf-8")
    (work / "b.json").write_text(json.dumps({"yield_pct": 28}), encoding="utf-8")
    rep = ni.run_numeric_integrity(work, report_text="")
    assert ni.count_conflicts(rep) == 0


def test_single_artifact_no_cross_conflict(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.json").write_text(
        json.dumps({"lhv": 7.29, "power": 20.0}), encoding="utf-8")
    rep = ni.run_numeric_integrity(work, report_text="")
    assert ni.count_conflicts(rep) == 0
