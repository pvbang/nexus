"""Tests for the tautology prober — runtime confirmation of static suspects.

Proves the prober (1) CONFIRMS a genuinely self-cancelling closure by re-running
it under varied inputs and seeing it never move, (2) REFUTES a false positive
(a metric that does respond), and (3) stays SUSPECTED — never over-claims — when
the slice isn't safely reproducible. Includes the exact BUG-01 shape with a
dict-subscript dependency (r['nt']).
"""

from __future__ import annotations

from app.orchestration.tautology_detector import scan_source
from app.orchestration.tautology_prober import probe_scan, probe_suspect

# Forced-to-zero closure: E_out reconstructs LHV exactly → closure constant.
TAUTOLOGY = """
E_chem = LHV - Q1 - Q2
E_out = E_chem + Q1 + Q2
closure = abs(LHV - E_out) / LHV
"""

# Same flag structurally, but E_out has an extra independent term (LEAK) → the
# metric DOES move with inputs → the static flag is a false positive.
RESPONSIVE = """
E_chem = LHV - Q1 - Q2
E_out = E_chem + Q1 + Q2 + LEAK
closure = abs(LHV - E_out) / LHV
"""

# BUG-01 shape with a subscript dependency, as in the real script (r['nt']).
TAUTOLOGY_SUBSCRIPT = """
Q_sens = r['nt'] * cp * (T - 298.15)
E_chem = LHV - Q_evap - Q_char - Q_loss - Q_sens
E_out = E_chem + Q_evap + Q_char + Q_loss + Q_sens
closure = abs(LHV - E_out) / LHV * 100
"""


def _probe_first(src: str) -> str:
    scan = scan_source(src)
    assert scan.suspects, "detector should flag this first"
    return probe_suspect(src, scan.suspects[0])


def test_confirms_genuine_tautology():
    assert _probe_first(TAUTOLOGY) == "confirmed"


def test_refutes_false_positive():
    assert _probe_first(RESPONSIVE) == "refuted"


def test_confirms_tautology_with_subscript_dependency():
    assert _probe_first(TAUTOLOGY_SUBSCRIPT) == "confirmed"


def test_probe_scan_annotates_all_suspects():
    scan = probe_scan(TAUTOLOGY, scan_source(TAUTOLOGY))
    assert all(s.confirmed in ("confirmed", "refuted", "suspected") for s in scan.suspects)
    assert scan.suspects[0].confirmed == "confirmed"


def test_unreproducible_slice_stays_suspected():
    # Metric depends on a function call the safe interpreter won't run → no over-claim.
    src = (
        "E_chem = LHV - Q1\n"
        "E_out = E_chem + Q1\n"
        "closure = abs(external_solver(LHV) - E_out) / LHV\n"
    )
    scan = scan_source(src)
    if scan.suspects:  # detector may or may not flag; if it does, must stay suspected
        assert probe_suspect(src, scan.suspects[0]) == "suspected"


def test_context_promotes_confirmed_and_drops_refuted():
    from app.orchestration.tautology_detector import build_suspect_context

    confirmed_scan = probe_scan(TAUTOLOGY, scan_source(TAUTOLOGY))
    ctx = build_suspect_context({"calc/balance.py": confirmed_scan})
    assert "PROVEN" in ctx and "UNVERIFIED" in ctx

    refuted_scan = probe_scan(RESPONSIVE, scan_source(RESPONSIVE))
    # All suspects refuted at runtime → nothing to report.
    assert build_suspect_context({"calc/leak.py": refuted_scan}) == ""


def test_real_bug01_script_confirmed():
    # End-to-end on the actual audited script, if present in the repo workspace.
    import pathlib

    p = pathlib.Path("workspace_data/tasks/d8b78042/_work/gasification_equilibrium.py")
    if not p.exists():
        return  # workspace data is gitignored; skip when absent
    src = p.read_text(encoding="utf-8")
    scan = probe_scan(src, scan_source(src))
    closures = [s for s in scan.suspects if s.name == "closure"]
    assert closures and closures[0].confirmed == "confirmed"
