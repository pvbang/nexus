"""Group B/C (audit f188a4c4): with the meter/dB units recognized, CC-2 grounding
flags a fabricated deliverable headline that no structured artifact produced —
the exact 338.50 m reverse-fit number that slipped through — while leaving real
artifact-backed figures untouched (no false positive)."""
from __future__ import annotations

from app.orchestration.numeric_integrity import NumericFact, ground_report_numbers


def _radar_artifact_facts():
    # Mirrors what radar_budget_taskspec.py / its CSV actually produce on disk.
    return [
        NumericFact(label="Rmax_N1", value=421.26, unit="m", source="radar.csv"),
        NumericFact(label="Rmax_N32", value=1001.94, unit="m", source="radar.csv"),
        NumericFact(label="Pt", value=10.0, unit="W", source="radar.csv"),
        NumericFact(label="freq", value=9.5, unit="GHz", source="radar.csv"),
    ]


def test_fabricated_meter_headline_flagged_unsourced():
    facts = _radar_artifact_facts()
    report = "Single-pulse R_max = 338.50 m (Swerling I); N=80 gives 986.46 m."
    out = ground_report_numbers(report, facts)
    assert "338.50 m [UNSOURCED]" in out
    assert "986.46 m [UNSOURCED]" in out


def test_real_artifact_numbers_not_flagged():
    facts = _radar_artifact_facts()
    report = "Canonical R_max = 421.26 m; P_t = 10 W; f = 9.5 GHz."
    out = ground_report_numbers(report, facts)
    assert "[UNSOURCED]" not in out


def test_grounding_failopen_without_facts():
    # No structured facts → report returned unchanged (never block a pure-prose task).
    report = "R_max = 338.50 m headline"
    assert ground_report_numbers(report, []) == report
