"""P2 wired: a self-verification FAIL verdict in agent output caps progress <100.

Rerun ea36dbc1 showed 100% while its own verify script returned 5/7 PASS. The
orchestrator must read that structured verdict from the turn's agent output and
cap the reported progress so a failing self-check can't surface as complete."""

from __future__ import annotations

import json

import pytest

from app.orchestration.models import AgentResult, ExecutionState, TurnResult


class _MockLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def chat(self, messages, temperature=0.2, max_tokens=3072, **kwargs):
        return {"content": json.dumps(self._payload)}


@pytest.mark.asyncio
async def test_progress_capped_when_self_verify_reports_failures(orchestrator, workspace_dir):
    task_id = "selfverifycap01"  # no CONTRACT → artifact cap inert; isolate this gate
    state = ExecutionState(task_id=task_id, total_agents=["engineer"])
    state.user_message = "Design the gasifier dossier."
    orchestrator._progress_by_task[task_id] = 95
    orchestrator._progress_history_by_task[task_id] = [95]

    ar = AgentResult(
        agent_role="engineer", agent_name="Eng",
        output="Kết quả xác minh: 5/7 PASS\nCÓ 2 DELIVERABLE CHƯA ĐẠT (5 FAIL).",
        success=True,
    )
    turn = TurnResult(turn_number=6, agent_results=[ar])

    orchestrator._llm = _MockLLM({"progress_pct": 100, "decision": "continue", "objective_tracking": []})
    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )
    assert result.progress_pct < 100, "self-reported 5/7 PASS must cap progress below 100"


@pytest.mark.asyncio
async def test_clean_self_verify_does_not_cap(orchestrator, workspace_dir):
    task_id = "selfverifycap02"
    state = ExecutionState(task_id=task_id, total_agents=["engineer"])
    state.user_message = "Design the gasifier dossier."
    orchestrator._progress_by_task[task_id] = 95
    orchestrator._progress_history_by_task[task_id] = [95]

    ar = AgentResult(
        agent_role="engineer", agent_name="Eng",
        output="Xác minh toàn bộ: 7/7 PASS | 0 FAIL.",
        success=True,
    )
    turn = TurnResult(turn_number=6, agent_results=[ar])

    orchestrator._llm = _MockLLM({"progress_pct": 100, "decision": "continue", "objective_tracking": []})
    result = await orchestrator.evaluate_turn(
        turn_result=turn, state=state, user_message=state.user_message
    )
    # A clean 7/7 verdict must NOT be penalised by this gate (progress not pulled down by it).
    assert result.progress_pct >= 95
