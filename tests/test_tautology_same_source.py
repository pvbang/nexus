"""Detect the "compare two vars built from the SAME source" tautology (R2-TAUTOLOGY).

Round-2 audit (2026-07-03, task-095fc772 gasifier). The existing detector catches
the "whole − parts, summed back" shape. But the gasifier's energy_closure() used a
DIFFERENT self-cancelling shape:

    P_gross        = P_net_target + P_parasitic     # in power_chain()
    ...
    P_gross_needed = P_net_target + P_parasitic     # in energy_closure()
    P_gross_got    = chain["P_gross"]               # == P_gross above
    err = abs(P_gross_got - P_gross_needed) / P_gross_needed * 100   # ≡ 0 always

Both operands of the closure metric are built from the SAME source expression
(P_net_target + P_parasitic), so the metric is algebraically ~0 for every input —
a tautology the "whole−parts" detector does not recognise. scan_source must flag it
as a suspect (for the critic/prober to confirm), while a REAL closure metric whose
two sides come from independent computations stays clean.
"""

from __future__ import annotations

from app.orchestration.tautology_detector import scan_source


_GASIFIER_SHAPE = '''
def power_chain(P_net_target, P_parasitic):
    P_gross = P_net_target + P_parasitic
    return {"P_gross": P_gross}

def energy_closure(P_net_target, P_parasitic, chain):
    P_gross_needed = P_net_target + P_parasitic
    P_gross_got = chain["P_gross"]
    closure = abs(P_gross_got - P_gross_needed) / P_gross_needed * 100
    return {"closure": closure}
'''

_REAL_CLOSURE = '''
def model(m_fuel, LHV, cp, dT, char_frac):
    # LHS from independent first-law terms; RHS the fuel energy — genuinely different.
    E_out = P_syngas_chem + cp * dT + char_frac * LHV_char
    E_in = m_fuel * LHV
    residual = abs(E_in - E_out) / E_in * 100
    return {"residual": residual}
'''


def test_flags_same_source_closure_tautology():
    res = scan_source(_GASIFIER_SHAPE)
    assert not res.is_clean, "should flag the same-source closure tautology"
    assert any("closure" in s.name.lower() for s in res.suspects)


def test_real_closure_from_independent_terms_is_clean():
    res = scan_source(_REAL_CLOSURE)
    assert res.is_clean, f"real closure must not be flagged: {[s.detail for s in res.suspects]}"


def test_direct_two_var_same_expr():
    src = '''
def f(a, b):
    got = a + b
    needed = a + b
    residual = got - needed
    return residual
'''
    assert not scan_source(src).is_clean
