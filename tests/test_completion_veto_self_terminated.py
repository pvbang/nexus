"""Completion veto must hold even when the LLM self-asserts a terminated_reason.

Root cause S7 (cross-session audit 2026-06-30, Opus 4.6 run): the model, stuck at
a low capped progress, declared "complete" and rationalised it in prose ("the
progress counter is a reporting bug, the task is actually done"). The ground-truth
artifact veto only fired when the completion reason was the bare ``llm_decision``;
a model that ALSO emitted a ``terminated_reason`` flipped ``has_blocked_justification``
on, which both bypassed the low-progress gate AND — when the orchestrator trusted
that self-asserted reason — could escape the artifact veto. A task with declared
artifacts still missing on disk would then close as "✅ ALL TASKS COMPLETE".

A self-asserted ``terminated_reason`` is NOT a real blocked/refusal terminal
(those route through blocked_terminal / lawful_refusal in the gate, not through the
LLM's free-text field). So when CONTRACT artifacts are materially missing, the
completion must STILL be vetoed regardless of an LLM-supplied terminated_reason.
"""

from __future__ import annotations

import json

import pytest

from app.core import task_paths
from app.orchestration.models import AgentResult, ExecutionState, TurnResult


class _MockLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def chat(self, messages, temperature=0.2, max_tokens=3072, **kwargs):
        return {"content": json.dumps(self._payload)}


_CONTRACT = """# Deliverable Contract

## What the user receives
### Artifact 1: Phase 1 report
- **Location**: _work/redteam/phase_1/recon.md
- **Verify**: exists

### Artifact 2: Phase 2 report
- **Location**: _work/redteam/phase_2/enum.md
- **Verify**: exists

### Artifact 3: Executive summary
- **Location**: _work/redteam/executive_summary.md
- **Verify**: exists
"""


def _state(task_id: str):
    state = ExecutionState(task_id=task_id, total_agents=["pentester"])
    state.user_message = "Run a 6-phase red team and deliver all reports."
    ar = AgentResult(
        agent_role="pentester",
        agent_name="Lead Pentester",
        output="All deliverables exist — the 10% is just a progress-counter bug.",
    )
    return state, TurnResult(turn_number=1, agent_results=[ar])


def _write_contract(task_id: str) -> None:
    task_paths.context_file(task_id, "CONTRACT.md").write_text(_CONTRACT, encoding="utf-8")


@pytest.mark.asyncio
async def test_self_asserted_terminated_reason_does_not_escape_artifact_veto(
    orchestrator, workspace_dir
):
    """LLM says complete + invents terminated_reason, but 1 of 3 artifacts on disk
    → must NOT complete; progress capped below 100."""
    task_id = "vetoselftr01"
    state, turn = _state(task_id)
    _write_contract(task_id)
    work = task_paths.work_dir(task_id)
    (work / "redteam" / "phase_1").mkdir(parents=True)
    (work / "redteam" / "phase_1" / "recon.md").write_text("real recon", encoding="utf-8")

    orchestrator._llm = _MockLLM({
        "progress_pct": 100,
        "decision": "complete",
        # The S7 escape hatch: a free-text terminated_reason the model made up.
        "terminated_reason": "reporting bug — task actually complete",
        "objective_tracking": [
            {"objective": "phase1", "status": "DONE"},
            {"objective": "phase2", "status": "DONE"},
            {"objective": "exec_summary", "status": "DONE"},
        ],
    })

    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )

    assert result.is_complete is False, (
        "a self-asserted terminated_reason must not let a task complete with "
        "CONTRACT artifacts missing"
    )
    assert result.progress_pct < 100
    assert "artifact" in (result.next_turn_focus or "").lower()


@pytest.mark.asyncio
async def test_all_artifacts_present_still_completes_with_terminated_reason(
    orchestrator, workspace_dir
):
    """Sanity: when all artifacts exist, a terminated_reason does not BLOCK
    completion (the veto is about missing artifacts, not the reason field)."""
    task_id = "vetoselftr02"
    state, turn = _state(task_id)
    _write_contract(task_id)
    work = task_paths.work_dir(task_id)
    for rel in (
        "redteam/phase_1/recon.md",
        "redteam/phase_2/enum.md",
        "redteam/executive_summary.md",
    ):
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("content", encoding="utf-8")

    orchestrator._llm = _MockLLM({
        "progress_pct": 100,
        "decision": "complete",
        "terminated_reason": "all phases delivered",
        "objective_tracking": [
            {"objective": "phase1", "status": "DONE"},
            {"objective": "phase2", "status": "DONE"},
            {"objective": "exec_summary", "status": "DONE"},
        ],
    })

    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )

    assert result.is_complete is True
    assert result.progress_pct >= 100
