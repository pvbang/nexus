"""Tests for the ROOT-B fix: self-attested verdict surfacing + no auto-PASS.

Context (audit of task d8b78042 — "verification theater"): the ledger verifier
used to mark any unrecognised claim ``verified=True`` ("can't disprove → assume
true") and never even EXTRACTED self-graded verdicts like "energy closure 0.00%"
or "đã xác minh 20 kWe". The result: a turn resting entirely on tautological /
back-solved claims scored HIGH trust. These tests pin the corrected behaviour:

  1. Self-attested verdicts are extracted (pure function).
  2. They are counted as self-attested, never as verified.
  3. They are surfaced in the verification context as an explicit skepticism signal.
  4. Unrecognised claims no longer auto-pass as verified.
"""

from __future__ import annotations

from app.orchestration.ledger_verifier import (
    LedgerVerifier,
    extract_self_attested_claims,
)

# ───────────────────────── pure extractor ─────────────────────────


def test_extracts_closure_tautology_verdict():
    out = "Energy closure = 0.00% < 5% PASS. Foundation verified."
    claims = extract_self_attested_claims(out)
    assert claims, "should surface the self-graded closure/PASS verdict"
    assert any("closure" in c.lower() or "pass" in c.lower() for c in claims)


def test_extracts_vietnamese_verified_and_power_claim():
    out = "Phán quyết: đã xác minh hệ thống đạt 20.0 kWe end-to-end."
    claims = extract_self_attested_claims(out)
    assert claims
    assert any("20" in c for c in claims)


def test_extracts_fos_and_converged():
    out = "FoS = 156.3 PASS. Solver converged ier=1 in <0.01s."
    claims = extract_self_attested_claims(out)
    assert claims


def test_plain_progress_text_not_flagged():
    # Neutral status with no self-grading verb / metric verdict → nothing to flag.
    out = "Read input_parameters.csv and started sizing the cyclone body."
    assert extract_self_attested_claims(out) == []


def test_extractor_dedupes_and_caps():
    out = "\n".join(["closure 0.00% PASS"] * 10 + ["FoS 156 PASS", "đã xác minh 20 kWe"])
    claims = extract_self_attested_claims(out, limit=3)
    assert len(claims) <= 3


# ───────────────────────── verifier integration ─────────────────────────


def _verifier(tmp_path, monkeypatch):
    # Point task_paths at an isolated temp workspace so no real task is touched.
    from app.core import task_paths

    monkeypatch.setattr(task_paths, "work_dir", lambda tid: tmp_path / "work")
    monkeypatch.setattr(task_paths, "context_dir", lambda tid: tmp_path / "ctx")
    (tmp_path / "work").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ctx").mkdir(parents=True, exist_ok=True)
    return LedgerVerifier("testtask")


def test_self_attested_verdict_counts_as_self_attested_not_verified(tmp_path, monkeypatch):
    v = _verifier(tmp_path, monkeypatch)
    tv = v.verify_turn_claims(
        turn=9,
        agent_outputs=[{
            "agent_role": "physicist",
            "output": "Energy closure 0.00% PASS. đã xác minh đạt 20 kWe.",
            "tool_calls": [],
        }],
    )
    assert tv.claims_self_attested >= 1
    # The whole point: a turn of pure self-grading must NOT read as verified.
    assert tv.claims_verified == 0


def test_self_attested_surfaced_in_context_even_at_high_trust(tmp_path, monkeypatch):
    v = _verifier(tmp_path, monkeypatch)
    v.verify_turn_claims(
        turn=9,
        agent_outputs=[{
            "agent_role": "physicist",
            "output": "Energy closure 0.00% PASS. Foundation verified.",
            "tool_calls": [],
        }],
    )
    ctx = v.build_verification_context()
    assert "SELF-ATTESTED" in ctx
    assert "closure" in ctx.lower() or "pass" in ctx.lower()


def test_unrecognised_claim_no_longer_auto_passes(tmp_path, monkeypatch):
    v = _verifier(tmp_path, monkeypatch)
    # _verify_claim on an unknown type must not return verified=True any more.
    result = v._verify_claim({"type": "mystery_metric", "value": "FoS 156"})
    assert result.verified is False
    assert result.confidence < 0.7  # still not a hard FAIL — just unproven


def test_clean_factual_turn_keeps_high_trust(tmp_path, monkeypatch):
    # A turn with neutral, non-self-grading output should not trip the skepticism
    # signal — guards against false positives drowning real work.
    v = _verifier(tmp_path, monkeypatch)
    v.verify_turn_claims(
        turn=1,
        agent_outputs=[{
            "agent_role": "researcher",
            "output": "Collected husk proximate analysis and saved a parameter table.",
            "tool_calls": [],
        }],
    )
    ctx = v.build_verification_context()
    assert "SELF-ATTESTED" not in ctx
