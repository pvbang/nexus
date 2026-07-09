"""Regression test: progress stores must agree after evaluate_turn.

Audit C2 surfaced a drift: ``evaluate_turn`` recorded the raw LLM ``progress_pct``
into the ProgressTracker (used by the completion/stagnation gate next turn) BEFORE
the objective-tracking adjustment, but wrote the ADJUSTED value into
``state.progress_history`` and the returned ``EvaluationResult``. The two stores
then disagreed.

This drives the real ``evaluate_turn`` with a mocked LLM (no network) and an empty
temp workspace (so the orchestrator's read-tool loop is skipped — it only runs when
the workspace tree contains ``_work/`` or ``TASK_BRIEF``). The mock returns a raw
progress that the objective-tracking math then lowers, so a drift would be visible
as a mismatch between the tracker and the returned/persisted value.
"""

from __future__ import annotations

import json

import pytest

from app.orchestration.models import ExecutionState, TurnResult
from app.orchestration.models import AgentResult


class _MockLLM:
    """Minimal async LLM stub: returns a fixed evaluation JSON for chat()."""

    def __init__(self, payload: dict):
        self._payload = payload

    async def chat(self, messages, temperature=0.2, max_tokens=3072, **kwargs):
        return {"content": json.dumps(self._payload)}


def _state_with_one_turn(task_id: str) -> tuple[ExecutionState, TurnResult]:
    state = ExecutionState(task_id=task_id, total_agents=["developer"])
    # A turn that produced real output so empty-turn retry is not triggered.
    agent_res = AgentResult(
        agent_role="developer",
        agent_name="Developer",
        output="Implemented the feature and verified it builds.",
    )
    turn_result = TurnResult(turn_number=1, agent_results=[agent_res])
    state.user_message = "Build a small CLI tool."
    return state, turn_result


@pytest.mark.asyncio
async def test_tracker_matches_returned_progress_after_objective_adjust(orchestrator, workspace_dir):
    """The ProgressTracker value must equal the returned/persisted progress.

    LLM claims 80% but only 1 of 4 objectives is DONE, so the objective math caps
    progress at min(80, 25+10)=35. Whatever the final number is, all three stores
    (tracker, state.progress_history, EvaluationResult) must agree.
    """
    task_id = "abcdef000001"
    state, turn_result = _state_with_one_turn(task_id)

    payload = {
        "progress_pct": 80,            # raw, inflated
        "decision": "continue",
        "objective_tracking": [
            {"objective": "scaffold", "status": "DONE"},
            {"objective": "implement", "status": "IN_PROGRESS"},
            {"objective": "test", "status": "TODO"},
            {"objective": "document", "status": "TODO"},
        ],
    }
    orchestrator._llm = _MockLLM(payload)

    result = await orchestrator.evaluate_turn(
        turn_result=turn_result,
        state=state,
        user_message=state.user_message,
    )

    returned = result.progress_pct
    tracked = orchestrator._progress.current(task_id)
    persisted = state.progress_history[turn_result.turn_number]

    # The objective math must have lowered it below the raw 80.
    assert returned < 80, "objective adjustment should cap the inflated claim"
    # The core invariant: every store agrees on the SAME final number.
    assert tracked == returned, (
        f"ProgressTracker ({tracked}) drifted from returned progress ({returned})"
    )
    assert persisted == returned, (
        f"state.progress_history ({persisted}) drifted from returned progress ({returned})"
    )
    # History tail must also be the final value (feeds the stagnation gate next turn).
    assert orchestrator._progress.history(task_id)[-1] == returned
