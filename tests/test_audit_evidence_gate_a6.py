"""Tests for the audit_log evidence gate (audit A6).

audit_log previously returned success=True for ANY transaction, so an agent could
record a critical FINDING with no evidence and the audit trail looked complete but
was empty of proof — self-attestation at the audit layer. The gate now SURFACES an
unsubstantiated finding (does not silently bless it) while still logging it.

Recon/neutral actions (no severity / info / low) are unaffected — the action itself
is the event; only material findings (critical/high/medium, or exploit) need evidence.
"""

from __future__ import annotations

import json

import pytest

from app.mcp.tools.audit_log import _audit_log, audit_requires_evidence


# ───────────────────────── pure policy ─────────────────────────


@pytest.mark.parametrize("severity,category,expected", [
    ("critical", "", True),
    ("high", "recon", True),
    ("medium", "osint", True),
    ("low", "recon", False),     # low/info findings don't require evidence
    ("info", "recon", False),
    ("", "recon", False),        # a neutral recon action is the event itself
    ("", "exploit", True),       # an exploit action must show proof
    ("", "", False),
])
def test_audit_requires_evidence_matrix(severity, category, expected):
    assert audit_requires_evidence(severity, category) is expected


# ───────────────────────── _audit_log integration ─────────────────────────


@pytest.fixture
def _task_ctx(workspace_dir):
    """Set an active task context so audit_log can resolve a workspace."""
    from app.core.execution_context import current_task_id

    token = current_task_id.set("t-audit-a6")
    yield "t-audit-a6"
    current_task_id.reset(token)


@pytest.mark.asyncio
async def test_unsubstantiated_finding_is_flagged_not_blessed(_task_ctx):
    from app.core import task_paths

    out = await _audit_log({
        "action": "rce_confirm",
        "target": "acme-prod.net",
        "severity": "critical",  # material finding, but NO evidence provided
        "category": "exploit",
    })
    parsed = json.loads(out)
    # Still logged (success), but the output must WARN it is unsubstantiated.
    assert parsed.get("success") is True
    assert "unsubstantiated" in parsed.get("output", "").lower()
    # ACTIVITY.md must carry the warning badge, not a clean transaction.
    activity = task_paths.context_file("t-audit-a6", "ACTIVITY.md").read_text(encoding="utf-8")
    assert "UNSUBSTANTIATED" in activity


@pytest.mark.asyncio
async def test_finding_with_evidence_is_clean(_task_ctx):
    from app.core import task_paths

    out = await _audit_log({
        "action": "rce_confirm",
        "target": "acme-prod.net",
        "severity": "critical",
        "category": "exploit",
        "evidence": "OOB DNS callback received: id=abc123.collab.example; HTTP 200 with marker.",
    })
    parsed = json.loads(out)
    assert parsed.get("success") is True
    assert "unsubstantiated" not in parsed.get("output", "").lower()
    activity = task_paths.context_file("t-audit-a6", "ACTIVITY.md").read_text(encoding="utf-8")
    assert "UNSUBSTANTIATED" not in activity


@pytest.mark.asyncio
async def test_neutral_recon_action_not_flagged(_task_ctx):
    # A neutral recon action with no severity needs no evidence.
    out = await _audit_log({
        "action": "dns_lookup",
        "target": "acme-prod.net",
        "category": "recon",
    })
    parsed = json.loads(out)
    assert parsed.get("success") is True
    assert "unsubstantiated" not in parsed.get("output", "").lower()


@pytest.mark.asyncio
async def test_missing_action_still_errors(_task_ctx):
    out = await _audit_log({"target": "x"})
    parsed = json.loads(out)
    assert "error" in parsed
    assert parsed.get("success") is not True


# ── Normalize / strip behaviour must stay locked (review MINOR) ──

def test_audit_requires_evidence_normalizes_case_and_whitespace():
    assert audit_requires_evidence("CRITICAL", "") is True
    assert audit_requires_evidence("  high  ", "recon") is True
    assert audit_requires_evidence("", "  EXPLOIT ") is True
    assert audit_requires_evidence(None, None) is False  # no crash, no false-require


@pytest.mark.asyncio
async def test_whitespace_only_evidence_is_unsubstantiated(_task_ctx):
    # The .strip() branch is the crux of the fix: " " must count as NO evidence.
    out = await _audit_log({
        "action": "rce_confirm", "target": "acme-prod.net",
        "severity": "critical", "evidence": "   ",
    })
    assert "unsubstantiated" in json.loads(out).get("output", "").lower()


@pytest.mark.asyncio
async def test_neutral_recon_leaves_no_badge_in_activity(_task_ctx):
    from app.core import task_paths

    await _audit_log({"action": "dns_lookup", "target": "acme-prod.net", "category": "recon"})
    activity = task_paths.context_file("t-audit-a6", "ACTIVITY.md").read_text(encoding="utf-8")
    assert "UNSUBSTANTIATED" not in activity


# ── The unsubstantiated signal must reach the orchestration verifier (review IMPORTANT):
#    not stay a private nudge to the calling agent. ──

def test_ledger_verifier_counts_unsubstantiated_audit_in_tool_output():
    from app.orchestration.ledger_verifier import LedgerVerifier

    lv = LedgerVerifier("t-audit-a6")
    agent_outputs = [{
        "agent_role": "pentester",
        "output": "Completed the scan.",
        "tool_calls": [{
            "tool_name": "audit_log",
            "success": True,
            "output": (
                "Audit transaction logged: [rce_confirm] on [acme-prod.net] — "
                "⚠ UNSUBSTANTIATED: a finding of this severity needs evidence..."
            ),
        }],
    }]
    tv = lv.verify_turn_claims(turn=3, agent_outputs=agent_outputs)
    # The unsubstantiated finding must register as a self-attested (un-reviewed) claim,
    # so the A3 completion gate can see it — not just the calling agent.
    assert tv.claims_self_attested >= 1
    assert any("unsubstantiated" in s.lower() for s in tv.self_attested)


def test_ledger_verifier_ignores_clean_audit_tool_output():
    from app.orchestration.ledger_verifier import LedgerVerifier

    lv = LedgerVerifier("t-audit-a6")
    agent_outputs = [{
        "agent_role": "pentester",
        "output": "Completed the scan.",
        "tool_calls": [{
            "tool_name": "audit_log",
            "success": True,
            "output": "Audit transaction logged: [dns_lookup] on [acme-prod.net]",
        }],
    }]
    tv = lv.verify_turn_claims(turn=3, agent_outputs=agent_outputs)
    assert tv.claims_self_attested == 0
