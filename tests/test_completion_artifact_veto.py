"""Integration: completion is vetoed when CONTRACT artifacts are missing.

Task 3c44a93f bug #4c end-to-end: the LLM voluntarily claims "complete" at high
progress, but the CONTRACT declares artifacts that do not exist on disk. The
orchestrator must (a) cap progress below 100 and (b) refuse to mark the task
complete on a bare ``llm_decision`` while declared artifacts are missing —
while still allowing completion once they all exist.
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


def _state(task_id: str) -> tuple[ExecutionState, TurnResult]:
    state = ExecutionState(task_id=task_id, total_agents=["pentester"])
    state.user_message = "Run a 6-phase red team and deliver all reports."
    ar = AgentResult(
        agent_role="pentester",
        agent_name="Lead Pentester",
        output="Phase 1 recon template written; everything is done.",
    )
    return state, TurnResult(turn_number=1, agent_results=[ar])


def _write_contract(task_id: str) -> None:
    task_paths.context_file(task_id, "CONTRACT.md").write_text(_CONTRACT, encoding="utf-8")


@pytest.mark.asyncio
async def test_complete_vetoed_when_artifacts_missing(orchestrator, workspace_dir):
    """LLM says complete@100 with only 1 of 3 CONTRACT artifacts on disk →
    not complete, progress capped well below 100."""
    task_id = "vetotk000001"
    state, turn = _state(task_id)
    _write_contract(task_id)
    # Only artifact 1 exists.
    work = task_paths.work_dir(task_id)
    (work / "redteam" / "phase_1").mkdir(parents=True)
    (work / "redteam" / "phase_1" / "recon.md").write_text("real recon", encoding="utf-8")

    orchestrator._llm = _MockLLM({
        "progress_pct": 100,
        "decision": "complete",
        "objective_tracking": [
            {"objective": "phase1", "status": "DONE"},
            {"objective": "phase2", "status": "DONE"},
            {"objective": "exec_summary", "status": "DONE"},
        ],
    })

    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )

    assert result.is_complete is False, "must not complete with artifacts missing"
    assert result.progress_pct < 100, "progress must be capped below 100"
    # Steering text should name the gap so the next turn produces the artifacts.
    assert "artifact" in (result.next_turn_focus or "").lower()


@pytest.mark.asyncio
async def test_complete_allowed_when_all_artifacts_present(orchestrator, workspace_dir):
    """When all 3 CONTRACT artifacts exist, a clean llm complete is honored."""
    task_id = "vetotk000002"
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
        "objective_tracking": [
            {"objective": "phase1", "status": "DONE"},
            {"objective": "phase2", "status": "DONE"},
            {"objective": "exec_summary", "status": "DONE"},
        ],
    })

    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )

    assert result.is_complete is True, "all artifacts present → completion honored"
    assert result.progress_pct >= 100
