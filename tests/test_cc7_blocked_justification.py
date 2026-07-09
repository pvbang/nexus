"""P2-C follow-up (CC-7): the low-progress completion guard must read a REAL
justification signal from the evaluation, not fields the LLM never emits.

Review found `_blocked_justified` keyed on `blocked_by_design`/`infeasible` —
neither is in the evaluation JSON schema the LLM is told to produce, so the guard
was effectively never satisfied except via a (then-buggy) refusal match. The
schema DOES carry `objective_tracking` with a "BLOCKED" status (prompts.py tells
the model to mark infeasible objectives BLOCKED). These tests pin that an
objective marked BLOCKED justifies a low-progress completion, while an ordinary
low-progress 'complete' with no blocked objective is still refused.
"""

from __future__ import annotations

import json

import pytest

from app.orchestration.models import AgentResult, ExecutionState, TurnResult


class _MockLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def chat(self, messages, temperature=0.2, max_tokens=3072, **kwargs):
        return {"content": json.dumps(self._payload)}


def _state(task_id: str) -> tuple[ExecutionState, TurnResult]:
    state = ExecutionState(task_id=task_id, total_agents=["developer"])
    state.user_message = "Attack the target and exfiltrate data."  # security-ish
    ar = AgentResult(agent_role="developer", agent_name="Dev",
                     output="Investigated; the objective cannot be met under current scope.")
    return state, TurnResult(turn_number=1, agent_results=[ar])


@pytest.mark.asyncio
async def test_blocked_objective_justifies_low_progress_complete(orchestrator, workspace_dir):
    task_id = "cc700000bloc"
    state, turn_result = _state(task_id)
    orchestrator._progress_by_task[task_id] = 20
    payload = {
        "progress_pct": 20,
        "decision": "complete",
        "objective_tracking": [
            {"objective": "primary goal", "status": "BLOCKED"},
        ],
    }
    orchestrator._llm = _MockLLM(payload)
    result = await orchestrator.evaluate_turn(
        turn_result=turn_result, state=state, user_message=state.user_message,
    )
    # A BLOCKED objective is a legitimate blocked-by-design justification → complete.
    assert result.is_complete is True


@pytest.mark.asyncio
async def test_unjustified_low_progress_complete_refused(orchestrator, workspace_dir):
    task_id = "cc700000unju"
    state, turn_result = _state(task_id)
    orchestrator._progress_by_task[task_id] = 20
    payload = {
        "progress_pct": 20,
        "decision": "complete",
        "objective_tracking": [
            {"objective": "primary goal", "status": "IN_PROGRESS"},
        ],
    }
    orchestrator._llm = _MockLLM(payload)
    result = await orchestrator.evaluate_turn(
        turn_result=turn_result, state=state, user_message=state.user_message,
    )
    # No blocked objective, no refusal → low static progress 'complete' is refused.
    assert result.is_complete is False
