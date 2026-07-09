"""P2-A (CC-1, cheap half): file-exists / count must not auto-PASS.

Audit 2026-06-22 (`350d9a50`): `_verify_file_exists` returned verified=True
confidence=0.95 for ANY file that exists — including a 0-byte file. A fabricated
report passes the gate just by existing. `_verify_count` auto-passed an
unverifiable small count (verified=True confidence=0.3). These pin the fix:
an empty file is a FAILED claim (confidence ≥0.7 so it reaches claims_failed),
and an unverifiable count is no longer verified=True.
"""

from __future__ import annotations

from app.orchestration.ledger_verifier import LedgerVerifier


def test_empty_file_is_not_verified(workspace_dir):
    v = LedgerVerifier("tsk")
    (v._workspace / "report.md").write_text("", encoding="utf-8")
    r = v._verify_file_exists("report.md", source="agent")
    assert r.verified is False
    assert r.confidence >= 0.7  # reaches claims_failed → blocks completion


def test_nonempty_file_still_verified(workspace_dir):
    v = LedgerVerifier("tsk")
    (v._workspace / "report.md").write_text("real content here", encoding="utf-8")
    r = v._verify_file_exists("report.md", source="agent")
    assert r.verified is True


def test_unverifiable_count_not_auto_passed(workspace_dir):
    v = LedgerVerifier("tsk")
    # No VAULT corroboration; small expected count used to auto-pass verified=True.
    r = v._verify_count("items", "7", source="agent")
    assert r.verified is False
