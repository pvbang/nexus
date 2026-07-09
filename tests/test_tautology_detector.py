"""Tests for the static tautology detector (ROOT-B completion).

Pins the detector against the exact self-cancelling closure shape from task
d8b78042 (BUG-01) plus its dict/kwarg variants, and guards precision: genuine
closure checks and unrelated arithmetic must NOT be flagged.
"""

from __future__ import annotations

from app.orchestration.tautology_detector import build_suspect_context, scan_source

# The real BUG-01 shape: E_chem = whole − parts; E_out = E_chem + parts; closure
# compares whole against E_out → forced to 0. Written as a dict() kwarg, like the original.
BUGGY = """
def closed_balance(LHV_dry, Q_evap, Q_char, Q_loss):
    E_chem = LHV_dry - Q_evap - Q_char - Q_loss
    E_out = E_chem + Q_evap + Q_char + Q_loss
    return dict(closure=abs(LHV_dry - E_out) / LHV_dry * 100)
"""

# Same tautology but as a plain assignment.
BUGGY_ASSIGN = """
E_chem = LHV - Q1 - Q2
E_out = E_chem + Q1 + Q2
closure = abs(LHV - E_out) / LHV
"""

# A genuine closure: E_out is summed from INDEPENDENTLY computed quantities,
# not reconstructed from (whole − parts). Must NOT be flagged.
GENUINE = """
E_chem = compute_chemical_energy(syngas, flow)
E_loss = measure_heat_loss(shell_temp)
E_out = E_chem + E_loss
closure = abs(E_fuel_in - E_out) / E_fuel_in * 100
"""


def test_detects_dict_kwarg_tautology():
    res = scan_source(BUGGY)
    assert not res.is_clean
    assert res.suspects[0].kind == "tautology"
    assert res.suspects[0].name == "closure"


def test_detects_plain_assignment_tautology():
    res = scan_source(BUGGY_ASSIGN)
    assert not res.is_clean
    assert any(s.name == "closure" for s in res.suspects)


def test_genuine_closure_not_flagged():
    # E_out here is NOT reconstructed from (E_fuel_in − parts) → no tautology.
    assert scan_source(GENUINE).is_clean


def test_unrelated_arithmetic_not_flagged():
    src = "a = b - c - d\ntotal = x + y + z\nprint(a, total)"
    assert scan_source(src).is_clean


def test_syntax_error_is_clean_not_crash():
    assert scan_source("def (oops:\n  return").is_clean


def test_build_context_renders_when_suspects_exist():
    res = scan_source(BUGGY)
    ctx = build_suspect_context({"calc/balance.py": res})
    assert "self-cancelling" in ctx.lower() or "verifies nothing" in ctx.lower()
    assert "calc/balance.py" in ctx


def test_build_context_empty_when_clean():
    assert build_suspect_context({}) == ""
