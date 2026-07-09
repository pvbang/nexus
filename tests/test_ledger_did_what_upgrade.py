"""Pin the ledger did_what-upgrade fix (audit 2026-06-23).

Each agent's turn is written to the ledger TWICE: an interim draft mid-turn (so
same-turn sequential agents can query structured context) and an authoritative
conclusion at end of turn (orchestrator notes + extracted summary + truncation
manifest). The interim draft used a raw output tail (`output[-500:][:300]`) so
its `did_what` always started mid-word and lost the agent's `## Summary`; the
end-of-turn write — which carries the high-quality notes — was SKIPPED for any
agent that already had a draft (`if role in existing_roles: continue`), so the
better conclusion was silently dropped and `truncated_topics` was never filled.

Fix: the ledger holds ONE authoritative record per (turn, agent_role); the
end-of-turn write UPGRADES the draft in place (`upsert_batch`) instead of
appending a duplicate or skipping. key_data is unioned so no mid-turn data point
is lost. These tests pin that contract.
"""

from __future__ import annotations

from app.orchestration.context_ledger import ContextLedger, LedgerEntry
from app.orchestration.models import AgentResult, EvaluationResult, TurnPlan, TurnResult


# ───────────────────────── upsert_batch (core) ─────────────────────────


def test_upsert_replaces_interim_no_duplicate(workspace_dir):
    led = ContextLedger("tsk")
    # Interim draft as the turn executor writes it (one line per agent per turn).
    led.append(LedgerEntry(
        turn=1, agent_role="developer", agent_name="Sim Eng",
        did_what="ail frag starting mid-word", key_data=["found X early"],
    ))
    # End-of-turn authoritative conclusion for the SAME (turn, role).
    led.upsert_batch([LedgerEntry(
        turn=1, agent_role="developer", agent_name="Sim Eng",
        did_what="Built the detection-budget model and proved 1km margin.",
        key_data=["margin +6.4 dB"], truncated_topics=["execute_command:radar"],
    )])

    dev = [e for e in led.query(since_turn=1, until_turn=1) if e.agent_role == "developer"]
    assert len(dev) == 1, "upgrade must not append a duplicate line"
    assert dev[0].did_what.startswith("Built the detection-budget"), "final did_what wins"
    # No fact found mid-turn is lost — key_data is unioned.
    assert "found X early" in dev[0].key_data
    assert "margin +6.4 dB" in dev[0].key_data
    # Truncation manifest (search hint), absent from the interim draft, is restored.
    assert dev[0].truncated_topics == ["execute_command:radar"]


def test_upsert_appends_when_no_interim(workspace_dir):
    led = ContextLedger("tsk")
    led.upsert_batch([LedgerEntry(
        turn=1, agent_role="_discussion_synthesis", agent_name="Team Consensus",
        did_what="Agreed to use Skolnik range equation.",
    )])
    entries = led.query(since_turn=1, until_turn=1)
    assert len(entries) == 1
    assert entries[0].agent_role == "_discussion_synthesis"


def test_upsert_persists_to_disk(workspace_dir):
    led = ContextLedger("tsk")
    led.append(LedgerEntry(turn=2, agent_role="developer", agent_name="X", did_what="draft"))
    led.upsert_batch([LedgerEntry(
        turn=2, agent_role="developer", agent_name="X", did_what="final conclusion",
    )])
    # Cold reload reads the file — proves the upgrade was rewritten, not cache-only.
    reloaded = ContextLedger("tsk")
    dev = [e for e in reloaded.query(since_turn=2, until_turn=2) if e.agent_role == "developer"]
    assert len(dev) == 1
    assert dev[0].did_what == "final conclusion"


# ───────────────────────── _write_ledger_entries (final path) ──────────


def test_final_write_upgrades_interim_with_notes(workspace_dir):
    from app.orchestration.pipeline import NexusExecutionPipeline

    led = ContextLedger("tsk")
    # Interim draft already on disk (every agent gets one mid-turn).
    led.append(LedgerEntry(
        turn=1, agent_role="developer", agent_name="Sim Eng",
        did_what="raw output tail, cut mid-word", key_data=["interim fact"],
    ))

    plan = TurnPlan(turn_number=1, objective="model the detection budget")
    result = AgentResult(
        agent_name="Sim Eng", agent_role="developer",
        output="x" * 600, success=True, tool_calls=[],
    )
    turn_result = TurnResult(turn_number=1, agent_results=[result])
    evaluation = EvaluationResult(
        turn_number=1,
        agent_evaluations=[{
            "agent": "Sim Eng", "role": "developer", "completed": True,
            "notes": "Orchestrator assessment: derived SNR(R), verified 1km margin.",
        }],
    )

    NexusExecutionPipeline._write_ledger_entries(led, turn_result, plan, evaluation)

    dev = [e for e in led.query(since_turn=1, until_turn=1) if e.agent_role == "developer"]
    assert len(dev) == 1, "final write must upgrade the interim draft, not duplicate it"
    assert dev[0].did_what == "Orchestrator assessment: derived SNR(R), verified 1km margin."
    assert "interim fact" in dev[0].key_data, "mid-turn key_data must survive the upgrade"
