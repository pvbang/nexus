"""CC-7 low-progress gate must ignore a self-asserted terminated_reason (E4).

Run 68c276ac closed at 10% progress with 4/4 agents failing. The orchestrator
computes `_blocked_justified` as a union that INCLUDED `eval_data.terminated_reason`
— a free-text field the LLM is never instructed (by prompts.py schema) to emit.
A model that invents `"terminated_reason": "task cannot be completed ..."` while
declaring `decision: complete` thereby flipped `has_blocked_justification` on and
walked straight through the CC-7 low-progress veto → complete@10%.

The sibling artifact-veto (test_completion_veto_self_terminated) already refuses
to let a self-asserted reason escape when CONTRACT artifacts are missing. This pins
the OTHER door: the CC-7 gate itself. A legitimate honest-halt terminal
(lawful_refusal / evolution_terminate / semantic_loop / loop_exhausted) is set by
CODE on the evaluation object AFTER this gate — never via the LLM's eval_data — so
removing the self-asserted field from the justification union cannot suppress a
real terminal.
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


def _state(task_id: str):
    state = ExecutionState(task_id=task_id, total_agents=["engineer"])
    state.user_message = "Design a manufacturing-ready gasifier dossier."
    ar = AgentResult(
        agent_role="engineer",
        agent_name="Mechanical Engineer",
        output="I could not finish the CAD drawings this turn.",
        success=False,
    )
    return state, TurnResult(turn_number=8, agent_results=[ar])


@pytest.mark.asyncio
async def test_self_asserted_terminated_reason_does_not_unlock_cc7(
    orchestrator, workspace_dir
):
    """Low progress + self-invented terminated_reason + no BLOCKED objective →
    the CC-7 gate must NOT be unlocked; the task must not complete."""
    task_id = "cc7selftr01"  # no CONTRACT.md written → artifact-veto path inert
    state, turn = _state(task_id)
    # Seed prior progress ≈ current so the "climbing" guard (progress_delta > 5)
    # does not pre-empt the CC-7 gate we are actually testing. Keep history short
    # so stagnation/plateau ceilings (need a longer window) also stay inert.
    orchestrator._progress_by_task[task_id] = 10
    orchestrator._progress_history_by_task[task_id] = [10]

    orchestrator._llm = _MockLLM({
        "progress_pct": 10,
        "decision": "complete",
        # The escape hatch: a free-text reason the LLM invented (not in schema).
        "terminated_reason": "the task cannot be completed without more turns",
        "objective_tracking": [
            {"objective": "cad_drawings", "status": "IN_PROGRESS"},
            {"objective": "simulation", "status": "NOT_STARTED"},
        ],
    })

    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )

    assert result.is_complete is False, (
        "a self-asserted terminated_reason must not unlock the CC-7 low-progress "
        "completion gate at 10%"
    )


@pytest.mark.asyncio
async def test_genuinely_blocked_objective_still_allows_low_progress_complete(
    orchestrator, workspace_dir
):
    """Sanity: the SCHEMA-backed justification (an objective marked BLOCKED) must
    still let a low-progress completion through — we only removed the free-text
    self-asserted field, not the legitimate blocked signal."""
    task_id = "cc7selftr02"
    state, turn = _state(task_id)
    orchestrator._progress_by_task[task_id] = 30
    orchestrator._progress_history_by_task[task_id] = [30]

    orchestrator._llm = _MockLLM({
        "progress_pct": 30,
        "decision": "complete",
        "objective_tracking": [
            {"objective": "recon", "status": "DONE"},
            {"objective": "exploit", "status": "BLOCKED"},
        ],
    })

    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )

    assert result.is_complete is True, (
        "a BLOCKED objective (schema-backed) is a legitimate justification and "
        "must still permit an honest low-progress completion"
    )
