"""P2-B (CC-2 + CC-3): ground report numbers + reconcile conflicting values.

Audit 2026-06-22:
  - CC-3 (`367a9793`, `cd7b60d8`, `f60ca7c8`): the same quantity carried ≥2
    different values across artifacts/report (rice 26.24 vs 30.82 kg/h; operating
    hours 4800 vs 7200) with NO reconciliation — two files both self-labeled
    "canonical/LOCKED" yet disagreed.
  - CC-2 (`350d9a50`, `367a9793`): headline numbers in the report that no script
    produced (488/243 trades; hand-typed 26.24 kg/h).

One module reads the structured artifacts (`_work/*.json|*.csv`) once and answers
both: report grounding (CC-2) and cross-source reconciliation (CC-3). Structured
artifacts (script output) are the source of truth; prose is the weak source.
"""

from __future__ import annotations

import json

import pytest

from app.orchestration import numeric_integrity as ni


# --- build_numeric_facts: structured artifacts ------------------------------

def test_build_facts_from_json(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.json").write_text(json.dumps({"power_kwe": 20.0, "eta_pct": 28}),
                                    encoding="utf-8")
    facts = ni.build_numeric_facts(work)
    labels = {f.label.lower(): f.value for f in facts}
    assert labels.get("power_kwe") == 20.0
    assert labels.get("eta_pct") == 28


# --- CC-3 reconciliation ----------------------------------------------------

def test_conflict_detected_artifact_vs_prose(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    # Prose claims a different value for the SAME labelled quantity.
    report = "The system delivers power_kwe = 14.3 at the shaft."
    rep = ni.run_numeric_integrity(work, report_text=report)
    assert ni.count_conflicts(rep) >= 1
    assert rep.has_conflict


def test_no_conflict_when_values_agree(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    report = "power_kwe = 20.0 confirmed across the board."
    rep = ni.run_numeric_integrity(work, report_text=report)
    assert ni.count_conflicts(rep) == 0


def test_same_value_many_places_no_false_positive(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "a.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    (work / "b.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    rep = ni.run_numeric_integrity(work, report_text="power_kwe is 20.0 everywhere")
    assert ni.count_conflicts(rep) == 0


def test_csv_timeseries_no_false_conflict(tmp_path):
    # A CSV column is a time-series: many rows, many values, ONE label. The report
    # citing one of those values (the latest) must NOT be flagged as a conflict
    # against the others — that wrongly blocked completion on any task with a log/
    # backtest/financial CSV plus a report stating a concrete figure.
    import csv as _csv
    work = tmp_path / "_work"
    work.mkdir()
    with open(work / "revenue.csv", "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["month", "revenue"])
        w.writerow(["jan", 100])
        w.writerow(["feb", 200])
        w.writerow(["mar", 300])
    rep = ni.run_numeric_integrity(work, report_text="Total revenue reached 300 in March.")
    assert ni.count_conflicts(rep) == 0


def test_csv_value_absent_from_column_is_conflict(tmp_path):
    # If the report cites a value that matches NO row of the column, that IS a
    # genuine conflict (the figure is not in the data).
    import csv as _csv
    work = tmp_path / "_work"
    work.mkdir()
    with open(work / "revenue.csv", "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["revenue"])
        w.writerow([100])
        w.writerow([200])
    rep = ni.run_numeric_integrity(work, report_text="revenue was 999")
    assert ni.count_conflicts(rep) >= 1


def test_no_structured_artifacts_no_conflict(tmp_path):
    # Pure-prose research task: nothing structured to arbitrate → never block.
    work = tmp_path / "_work"
    work.mkdir()
    (work / "notes.md").write_text("we estimate around 50 widgets", encoding="utf-8")
    rep = ni.run_numeric_integrity(work, report_text="about 50 to 70 widgets")
    assert ni.count_conflicts(rep) == 0


# --- CC-2 grounding ---------------------------------------------------------

def test_different_units_same_label_not_conflict(tmp_path):
    # Same label token but different units name different quantities (28% vs 28 kg).
    # Comparing their raw values is meaningless → must NOT be a conflict.
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.json").write_text(json.dumps({"yield_pct": 28}), encoding="utf-8")
    # Prose mentions a yield of 14 kg/h — different unit, not the same quantity.
    rep = ni.run_numeric_integrity(work, report_text="yield_pct delivered 14 kg/h")
    assert ni.count_conflicts(rep) == 0


def test_unsourced_headline_flagged(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    # Report cites a figure with units that no artifact produced.
    report = "Final throughput reached 26.24 kg/h after tuning."
    grounded = ni.ground_report_numbers(report, ni.build_numeric_facts(work))
    assert "[UNSOURCED]" in grounded


def test_sourced_number_not_flagged(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    (work / "calc.json").write_text(json.dumps({"power_kwe": 20.0}), encoding="utf-8")
    report = "The model outputs 20.0 kWe."
    grounded = ni.ground_report_numbers(report, ni.build_numeric_facts(work))
    assert "[UNSOURCED]" not in grounded


def test_ground_failopen_no_artifacts(tmp_path):
    work = tmp_path / "_work"
    work.mkdir()
    report = "some prose with 12.5 kg/h figure"
    grounded = ni.ground_report_numbers(report, ni.build_numeric_facts(work))
    # No structured source to check against → return unchanged (no false flags).
    assert grounded == report
