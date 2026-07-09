"""The orchestrator evaluation prompt must forbid INVENTING concrete data in
assignments (audit S6).

Root cause S6 (cross-session audit 2026-06-30, Opus 4.6 run): the orchestrator's
next-turn assignment literally instructed the agent to write a DNS table with
"A: 202.46.1.34" — an IP no tool ever resolved (the real tool output was
119.252.170.162). The orchestrator HALLUCINATED a concrete fact and ordered the
agent to record it, so fabrication originated at the assignment-generation layer,
upstream of the agents.

The evaluation prompt (which emits next_assignments) must tell the orchestrator to
never embed invented concrete values (IPs, ASNs, certs, CVEs, credentials) in an
assignment, and to reference real tool output / the ledger instead.
"""

from __future__ import annotations

from app.orchestration.prompts import build_orchestrator_evaluation_prompt


def test_eval_prompt_forbids_inventing_concrete_values_in_assignments():
    p = build_orchestrator_evaluation_prompt().lower()
    assert "next_assignments" in p
    # Must forbid putting invented/fabricated concrete values into an assignment.
    assert ("do not invent" in p or "never invent" in p or "do not fabricate" in p
            or "never fabricate" in p)
    # Must steer toward real tool output / ledger as the source of any concrete datum.
    assert "tool output" in p or "ledger" in p or "real data" in p
