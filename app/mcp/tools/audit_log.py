"""
Nexus - Audit Log Tool.

Provides agents with the ability to record audit transactions directly
into ACTIVITY.md. Used by security/intelligence agents to create immutable
audit trails of actions taken during investigations, pentests, and forensics.

Every significant action (scan, query, trace, verify) should be logged
so the full chain of evidence is preserved and auditable.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)

# A material finding must carry evidence; a neutral recon action need not (the
# action itself is the recorded event). Mirrors the A3 doctrine: a "finding" with
# no proof is self-attestation, so it is surfaced as UNSUBSTANTIATED rather than
# logged as a clean transaction.
_EVIDENCE_REQUIRED_SEVERITIES = {"critical", "high", "medium"}
_EVIDENCE_REQUIRED_CATEGORIES = {"exploit"}


def audit_requires_evidence(severity: str, category: str) -> bool:
    """Whether this audit transaction asserts a finding that must carry evidence.

    True when the severity is material (critical/high/medium) or the category is an
    active exploitation step — both assert impact and need proof. Recon/neutral
    actions (no/low/info severity) do not: the action is the event.
    """
    return (
        (severity or "").strip().lower() in _EVIDENCE_REQUIRED_SEVERITIES
        or (category or "").strip().lower() in _EVIDENCE_REQUIRED_CATEGORIES
    )


async def _audit_log(args: dict[str, Any]) -> str:
    """Record an audit transaction in ACTIVITY.md."""
    from app.core.execution_context import current_task_id
    from app.orchestration.activity_logger import ActivityLogger

    task_id = current_task_id.get("")
    if not task_id:
        return tool_error("No active task context — audit_log requires a running task")

    action = args.get("action", "").strip()
    target = args.get("target", "").strip()

    if not action:
        return tool_error("'action' is required (what was done)")
    if not target:
        return tool_error("'target' is required (what it was performed against)")

    severity = args.get("severity", "")
    category = args.get("category", "")
    evidence = args.get("evidence", "").strip()
    # A material finding logged with no evidence is an unsubstantiated claim — log it
    # (audit trail must stay complete) but flag it instead of blessing it as clean.
    unsubstantiated = audit_requires_evidence(severity, category) and not evidence

    al = ActivityLogger(task_id)
    await al.log_audit_transaction(
        action=action,
        target=target,
        agent_name=args.get("agent_name", ""),
        details=args.get("details", ""),
        evidence=evidence,
        severity=severity,
        category=category,
        unsubstantiated=unsubstantiated,
    )

    if unsubstantiated:
        return tool_result(
            success=True,
            output=(
                f"Audit transaction logged: [{action}] on [{target}] — "
                "⚠ UNSUBSTANTIATED: a finding of this severity needs evidence, but none "
                "was provided. Re-log with `evidence=` (request/response, callback log, "
                "hash, file output) so the claim is verifiable; an unsubstantiated finding "
                "does not count as proven."
            ),
        )

    return tool_result(
        success=True,
        output=f"Audit transaction logged: [{action}] on [{target}]",
    )


registry.register(
    name="audit_log",
    toolset="audit",
    schema={
        "description": (
            "Record an audit transaction in the task's ACTIVITY.md. "
            "Use this to create an immutable trail of every significant action "
            "taken during security assessments, intelligence operations, "
            "forensic examinations, financial investigations, or any audit-sensitive work. "
            "Every scan, query, trace, verification, and finding should be logged."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "What was done. Examples: port_scan, cve_verify, wallet_trace, "
                        "dns_lookup, credential_check, evidence_acquire, ioc_validate, "
                        "network_capture, malware_triage, source_verify"
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "What it was performed against. Examples: 192.168.1.1, "
                        "example.com, 0x1234...abcd, CVE-2026-12345, sample.exe"
                    ),
                },
                "agent_name": {
                    "type": "string",
                    "description": "Name of the agent performing the action (auto-filled if omitted)",
                },
                "details": {
                    "type": "string",
                    "description": "Full description of action parameters, methodology, and rationale",
                },
                "evidence": {
                    "type": "string",
                    "description": "Key evidence or output produced (hashes, findings, IOCs, etc.)",
                },
                "severity": {
                    "type": "string",
                    "enum": ["critical", "high", "medium", "low", "info"],
                    "description": "Severity level of the finding (if applicable)",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "recon", "exploit", "forensics", "finint", "osint",
                        "network", "malware", "crypto", "sigint", "social_eng",
                        "threat_hunt", "counter_intel", "audit", "compliance",
                    ],
                    "description": "Category of the audit action",
                },
            },
            "required": ["action", "target"],
        },
    },
    handler=_audit_log,
)
