"""Integration: 3 consecutive Evolution TERMINATE signals force an honest halt.

Task 3c44a93f bug #4a end-to-end: when Evolution stays high-severity AND
stagnating for _CONSECUTIVE_TERMINATE_LIMIT turns, evaluate_turn must stop the
loop with terminated_reason='evolution_terminate' (an HONEST HALTED handoff),
instead of the orchestrator ignoring the advice and dispatching empty turns.
A new progress high-water-mark in between resets the streak.
"""

from __future__ import annotations

import json

import pytest

from app.orchestration.evolution_agent import EvolutionInsight
from app.orchestration.models import AgentResult, ExecutionState, TurnResult
from app.orchestration.termination import _CONSECUTIVE_TERMINATE_LIMIT


class _MockLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def chat(self, messages, temperature=0.2, max_tokens=3072, **kwargs):
        return {"content": json.dumps(self._payload)}


def _turn(n: int) -> TurnResult:
    ar = AgentResult(
        agent_role="pentester",
        agent_name="Lead Pentester",
        output="Same stagnant analysis, no new artifact this turn.",
    )
    return TurnResult(turn_number=n, agent_results=[ar])


def _terminate_insight() -> EvolutionInsight:
    return EvolutionInsight(
        improvement_suggestions=["TERMINATE NOW — no value-positive next dispatch."],
        quality_trajectory="stagnating",
        should_inject=True,
        severity_score=0.8,
    )


@pytest.mark.asyncio
async def test_consecutive_terminate_signals_halt_evaluate_turn(orchestrator, workspace_dir):
    task_id = "termint00001"
    state = ExecutionState(task_id=task_id, total_agents=["pentester"])
    state.user_message = "Stuck security task."
    # Flat low progress so no other ceiling fires and no new HWM resets the streak.
    orchestrator._llm = _MockLLM({"progress_pct": 20, "decision": "continue"})

    result = None
    for n in range(1, _CONSECUTIVE_TERMINATE_LIMIT + 1):
        result = await orchestrator.evaluate_turn(
            turn_result=_turn(n),
            state=state,
            user_message=state.user_message,
            evolution_context=_terminate_insight(),
        )
        state.turns_completed.append(_turn(n))

    assert result.is_complete is True, "must halt after consecutive terminate signals"
    assert result.terminated_reason == "evolution_terminate", (
        "halt must be an honest HALTED reason, not a fake complete"
    )


@pytest.mark.asyncio
async def test_new_progress_peak_resets_terminate_streak(orchestrator, workspace_dir):
    """A genuine new high-water-mark mid-stream must NOT be force-halted."""
    task_id = "termint00002"
    state = ExecutionState(task_id=task_id, total_agents=["pentester"])
    state.user_message = "Recovering task."

    # Two terminate signals at 20%, then a turn that climbs to 60% (new HWM),
    # which should reset the streak so the loop is not halted.
    seqs = [
        ({"progress_pct": 20, "decision": "continue"}, _terminate_insight()),
        ({"progress_pct": 20, "decision": "continue"}, _terminate_insight()),
        ({"progress_pct": 60, "decision": "continue"}, None),  # real progress
    ]
    result = None
    for n, (payload, insight) in enumerate(seqs, start=1):
        orchestrator._llm = _MockLLM(payload)
        result = await orchestrator.evaluate_turn(
            turn_result=_turn(n),
            state=state,
            user_message=state.user_message,
            evolution_context=insight,
        )
        state.turns_completed.append(_turn(n))

    assert result.is_complete is False, "a recovering task must not be force-halted"
    assert orchestrator._consecutive_terminate_by_task.get(task_id, 0) == 0
