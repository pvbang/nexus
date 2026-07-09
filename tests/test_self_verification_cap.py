"""Progress must not read 100% when the task's OWN verification reports failures.

Gasifier rerun ea36dbc1 (audit 2026-07-01): CONTEXT_INDEX showed 100%, but the
run's own verify_all_deliverables.py returned 5/7 PASS (2 FAIL), and the CAD did
not contain the design's throat number. The artifact-delivery cap only checks
files EXIST — it can't tell a passing deliverable from a failing one. This gate
reads the STRUCTURED verdict the agent's own verification emitted (e.g. "5/7 PASS"
or "2 FAIL") and caps progress below 100% when it reports failures.

Design (conservative, this is a completion gate):
- Only STRUCTURED verdicts count: "N/M PASS" (N<M) or "K FAIL" (K>0). Prose like
  "7/7 DELIVERABLES ĐẠT" is NOT trusted (that is exactly the theater to catch).
- Take the WORST (lowest ratio / any explicit failure) seen — a task can't be
  100% if any of its own checks failed.
- Fail-open: no structured verdict → None (no cap; never worse than today).
"""

from __future__ import annotations

from app.orchestration.termination import self_verification_cap


def test_ratio_verdict_with_failures_caps_below_100():
    cap = self_verification_cap("Kết quả: 5/7 PASS\nCÓ 2 DELIVERABLE CHƯA ĐẠT")
    assert cap is not None and cap < 100


def test_all_pass_ratio_does_not_cap():
    # 7/7 PASS is a clean self-verify → no cap from this gate.
    assert self_verification_cap("verify: 7/7 PASS | 0 FAIL") is None


def test_explicit_fail_count_caps():
    cap = self_verification_cap("Self-test summary: 5 FAIL across deliverables")
    assert cap is not None and cap < 100


def test_zero_fail_does_not_cap():
    assert self_verification_cap("all checks green: 0 FAIL") is None


def test_prose_pass_claim_is_not_trusted_alone():
    # A prose "7/7 ĐẠT" with NO structured PASS/FAIL verdict → no signal, fail-open.
    assert self_verification_cap("Kết luận: 7/7 DELIVERABLES ĐẠT — hoàn thành") is None


def test_no_verdict_returns_none():
    assert self_verification_cap("Tôi đã viết báo cáo và bản vẽ.") is None
    assert self_verification_cap("") is None


def test_worst_ratio_wins_across_multiple_verdicts():
    # Mixed output (many verify scripts): the lowest ratio governs.
    text = "module A: 10/10 PASS\nfinal gate: 5/7 PASS\nsub: 12/12 PASS"
    cap = self_verification_cap(text)
    # driven by 5/7 (~71% + headroom), not the clean 10/10s (which yield no cap)
    assert cap is not None and cap < 100 and cap <= 85


def test_ratio_uses_lowest_and_reflects_magnitude():
    # 5/7 → ~71%; cap should be in that neighbourhood (+ small headroom), < 100.
    cap = self_verification_cap("gate: 5/7 PASS")
    assert 60 <= cap <= 85
