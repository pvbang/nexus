"""Integration test: role rotation actually fires through plan_next_turn.

Unit tests pin enforce_role_rotation() in isolation; this proves the wiring —
that a real plan_next_turn() call, given the d8b78042 scenario (one role ran the
last 2 turns and the evaluator proposes it AGAIN), returns a plan whose stale
slot has been swapped to a fresh role (critic) with the refute briefing.
"""

from __future__ import annotations

import pytest

from app.orchestration.models import (
    AgentResult,
    EvaluationResult,
    ExecutionState,
    TurnResult,
)
from app.orchestration.nexus_orchestrator import NexusOrchestrator

TEAM = ["physicist", "engineer", "data_scientist", "developer", "writer", "critic"]


def _turn_run_by(turn_number: int, role: str) -> TurnResult:
    tr = TurnResult(turn_number=turn_number)
    tr.agent_results = [AgentResult(agent_role=role, agent_name=role.title(), output="ok")]
    return tr


@pytest.mark.asyncio
async def test_plan_next_turn_rotates_monoculture_to_critic():
    orch = NexusOrchestrator(llm=None)
    state = ExecutionState(
        task_id="f00dface0001",
        user_message="design a gasifier dossier",
        total_agents=list(TEAM),
    )
    # physicist ran both recent turns — the exact monoculture from the audit.
    state.turns_completed = [
        _turn_run_by(1, "physicist"),
        _turn_run_by(2, "physicist"),
    ]
    # Avoid the metacognition (LLM) branch: keep |progress_delta| >= 3.
    orch._progress_by_task["f00dface0001"] = 40

    evaluation = EvaluationResult(
        turn_number=2,
        progress_pct=50,
        is_complete=False,
        missing_items=["Reconcile the feed/flow numbers across the dossier"],
        next_turn_focus="Continue refining the equilibrium model.",
        # Evaluator again proposes ONLY physicist — nothing fresh on its own.
        next_assignments=[{"agent_role": "physicist", "task": "keep refining the model"}],
    )

    plan = await orch.plan_next_turn(state, evaluation)

    assert plan is not None
    roles = [a.agent_role for a in plan.assignments]
    assert "critic" in roles, f"rotation should inject critic, got {roles}"
    assert "physicist" not in roles, f"the stale slot should be swapped, got {roles}"
    critic = next(a for a in plan.assignments if a.agent_role == "critic")
    assert "REFUTE" in critic.task_description


@pytest.mark.asyncio
async def test_plan_next_turn_injects_starved_writer_for_report_gap():
    """Audit c527e1c2: engineer/critic alternated 11 turns, the writer NEVER ran,
    and the missing deliverable was the report. plan_next_turn must inject the
    starved writer (not another critic) with a WRITE-ONLY directive."""
    orch = NexusOrchestrator(llm=None)
    state = ExecutionState(
        task_id="c527e1c20001",
        user_message="counter-uas detection & tracking design dossier",
        total_agents=list(TEAM),
    )
    # 8 recent turns: engineer/critic alternate; physicist/data_scientist/developer/
    # writer never appear in the window → writer is starved (so are some others, but
    # the report gap makes the writer lead priority).
    alt = ["engineer", "critic", "engineer", "engineer", "critic", "engineer", "engineer", "critic"]
    state.turns_completed = [_turn_run_by(i + 1, r) for i, r in enumerate(alt)]
    orch._progress_by_task["c527e1c20001"] = 17

    evaluation = EvaluationResult(
        turn_number=8,
        progress_pct=17,
        is_complete=False,
        missing_items=[
            "Final consolidated report.md does not exist on disk",
            "Sections 2-5 (state vector, observability, IMM, track lifecycle) not written",
        ],
        next_turn_focus="Write the consolidated report.",
        # Evaluator AGAIN proposes engineer (the monoculture-dodging analytical loop).
        next_assignments=[{"agent_role": "engineer", "task": "re-derive the radar range equation"}],
    )

    plan = await orch.plan_next_turn(state, evaluation)

    assert plan is not None
    roles = [a.agent_role for a in plan.assignments]
    assert "writer" in roles, f"a report gap with a starved writer must inject writer, got {roles}"
    writer = next(a for a in plan.assignments if a.agent_role == "writer")
    # Writer gets a WRITE directive, NOT the inherited "re-derive radar" analytical task.
    assert "WRITE THE DELIVERABLE" in writer.task_description
    # The inherited analytical task ("re-derive the radar range equation") must be gone.
    assert "radar range equation" not in writer.task_description.lower()


@pytest.mark.asyncio
async def test_plan_next_turn_no_rotation_when_proposal_already_fresh():
    orch = NexusOrchestrator(llm=None)
    state = ExecutionState(
        task_id="f00dface0002",
        user_message="design a gasifier dossier",
        total_agents=list(TEAM),
    )
    state.turns_completed = [
        _turn_run_by(1, "physicist"),
        _turn_run_by(2, "physicist"),
    ]
    orch._progress_by_task["f00dface0002"] = 40

    evaluation = EvaluationResult(
        turn_number=2,
        progress_pct=50,
        is_complete=False,
        missing_items=["Produce the manufacturing drawings"],
        next_turn_focus="Generate DXF drawings.",
        # Evaluator already brings in a fresh role → no forced rotation.
        next_assignments=[{"agent_role": "developer", "task": "produce DXF drawings"}],
    )

    plan = await orch.plan_next_turn(state, evaluation)

    assert plan is not None
    roles = [a.agent_role for a in plan.assignments]
    assert "developer" in roles
    assert "critic" not in roles  # nothing to fix → no injection


@pytest.mark.asyncio
async def test_critic_briefing_points_at_static_audit_suspects(tmp_path, monkeypatch):
    # Closing the loop: when the static auditor flags a self-cancelling check in the
    # workspace, the rotated-in critic must be pointed straight at that file:line.
    from app.core import task_paths

    work = tmp_path / "_work"
    work.mkdir()
    (work / "balance.py").write_text(
        "E_chem = LHV - Q1 - Q2\n"
        "E_out = E_chem + Q1 + Q2\n"
        "closure = abs(LHV - E_out) / LHV\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(task_paths, "work_dir", lambda tid: work)

    orch = NexusOrchestrator(llm=None)
    state = ExecutionState(
        task_id="f00dface0003",
        user_message="design a gasifier dossier",
        total_agents=list(TEAM),
    )
    state.turns_completed = [_turn_run_by(1, "physicist"), _turn_run_by(2, "physicist")]
    orch._progress_by_task["f00dface0003"] = 40

    evaluation = EvaluationResult(
        turn_number=2,
        progress_pct=50,
        is_complete=False,
        missing_items=["Verify the energy balance closes"],
        next_turn_focus="Re-check the closure.",
        next_assignments=[{"agent_role": "physicist", "task": "recheck closure"}],
    )

    plan = await orch.plan_next_turn(state, evaluation)

    critic = next(a for a in plan.assignments if a.agent_role == "critic")
    # The closure here is a genuine tautology, so the prober CONFIRMS it at runtime
    # and the briefing escalates from "suspect" to "PROVEN … UNVERIFIED".
    assert "PROVEN" in critic.task_description
    assert "balance.py" in critic.task_description
    assert "closure" in critic.task_description
