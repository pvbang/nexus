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


# ── normalize_outcome (T7 hardening) ──────────────────────────────────────


def test_normalize_outcome_same_content_same_key():
    from app.orchestration.termination import normalize_outcome

    # Whitespace / case differences collapse to the same key.
    a = normalize_outcome("R_max = 421 m,  N=32  PASS")
    b = normalize_outcome("r_max = 421 m, n=32 pass")
    assert a == b and a != ""


def test_normalize_outcome_shared_long_prefix_differs_on_body():
    from app.orchestration.termination import normalize_outcome

    # A fixed >=120-char boilerplate header must NOT collapse two outputs whose
    # BODIES differ — the prefix-collision false positive that motivated T7.
    header = "## CRITIQUE\n" + "Reviewing the deliverable against the success criteria in detail. " * 3
    out_a = header + "\nFinding A: range is 421 m."
    out_b = header + "\nFinding B: NEES is out of bounds."
    assert len(header) > 120
    assert normalize_outcome(out_a) != normalize_outcome(out_b)


def test_normalize_outcome_blank_is_empty_key():
    from app.orchestration.termination import normalize_outcome

    assert normalize_outcome("") == ""
    assert normalize_outcome("   \n\t  ") == ""


def test_blank_outcome_never_terminal_even_if_repeated():
    # Empty agent outputs (e.g. failed turns) must not look like a repeated result.
    hist = [("critic", "")] * (_SEMANTIC_LOOP_TERMINATE_AT + 2)
    assert semantic_loop_terminal(hist) is False


def test_normalized_repeated_outcome_is_terminal():
    from app.orchestration.termination import normalize_outcome

    # Same content (varied whitespace/case) across turns → same key → terminal.
    key = normalize_outcome("Re-derived R_max = 421 m, N=32 PASS")
    hist = [("critic", key)] * _SEMANTIC_LOOP_TERMINATE_AT
    assert semantic_loop_terminal(hist) is True
