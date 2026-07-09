"""E7 wired end-to-end: barren-turn streak forces an honest halt in evaluate_turn.

Complements the pure-gate test (test_no_production_halt). Here we drive the real
`evaluate_turn` so the counter bookkeeping (increment on a fully-barren turn,
reset on ANY tool success) and the terminated_reason stamp are exercised together.
"""

from __future__ import annotations

import json

import pytest

from app.orchestration.models import AgentResult, ExecutionState, ToolCall, TurnResult
from app.orchestration.termination import _BARREN_TURNS_HALT_AT


class _MockLLM:
    def __init__(self, payload: dict):
        self._payload = payload

    async def chat(self, messages, temperature=0.2, max_tokens=3072, **kwargs):
        return {"content": json.dumps(self._payload)}


def _barren_turn(n: int) -> TurnResult:
    # An agent that produced prose but landed ZERO successful tool calls.
    ar = AgentResult(
        agent_role="engineer",
        agent_name="Engineer",
        output="I analysed the design at length but wrote nothing to disk.",
        tool_calls=[],
        success=True,  # prose-only turns are legitimately success=True (E3 doctrine)
    )
    return TurnResult(turn_number=n, agent_results=[ar])


def _productive_turn(n: int) -> TurnResult:
    ar = AgentResult(
        agent_role="engineer",
        agent_name="Engineer",
        output="Wrote the report.",
        tool_calls=[ToolCall(tool_name="write_file", output="ok", success=True)],
        success=True,
        produced_deliverable=True,
    )
    return TurnResult(turn_number=n, agent_results=[ar])


def _payload():
    # Keep the LLM steering the task forward (never self-completes) so ONLY the
    # barren-turn ceiling can end it.
    return {"progress_pct": 15, "decision": "continue", "objective_tracking": []}


@pytest.mark.asyncio
async def test_barren_streak_forces_honest_halt(orchestrator, workspace_dir):
    task_id = "barren01"
    state = ExecutionState(task_id=task_id, total_agents=["engineer"])
    state.user_message = "Build the dossier."
    orchestrator._llm = _MockLLM(_payload())

    result = None
    for n in range(1, _BARREN_TURNS_HALT_AT + 1):
        turn = _barren_turn(n)
        result = await orchestrator.evaluate_turn(
            turn_result=turn, state=state, user_message=state.user_message
        )
        state.turns_completed.append(turn)  # turn_count = len(turns_completed)

    assert result.is_complete is True, "N barren turns must force a halt"
    assert result.terminated_reason == "barren_no_production", (
        "the halt must be stamped as an honest halt, not a genuine completion"
    )


@pytest.mark.asyncio
async def test_one_tool_success_resets_barren_streak(orchestrator, workspace_dir):
    task_id = "barren02"
    state = ExecutionState(task_id=task_id, total_agents=["engineer"])
    state.user_message = "Build the dossier."
    orchestrator._llm = _MockLLM(_payload())

    # Three barren turns (one below the ceiling)...
    for n in range(1, _BARREN_TURNS_HALT_AT):
        turn = _barren_turn(n)
        await orchestrator.evaluate_turn(
            turn_result=turn, state=state, user_message=state.user_message
        )
        state.turns_completed.append(turn)
    # ...then a productive turn resets the streak...
    prod = _productive_turn(_BARREN_TURNS_HALT_AT)
    r_prod = await orchestrator.evaluate_turn(
        turn_result=prod, state=state, user_message=state.user_message,
    )
    state.turns_completed.append(prod)
    assert r_prod.terminated_reason != "barren_no_production"
    assert orchestrator._consecutive_barren_by_task.get(task_id, 0) == 0

    # ...so one more barren turn is only #1 again, NOT a halt.
    nxt = _barren_turn(_BARREN_TURNS_HALT_AT + 1)
    r_next = await orchestrator.evaluate_turn(
        turn_result=nxt, state=state, user_message=state.user_message,
    )
    assert r_next.terminated_reason != "barren_no_production", (
        "a single barren turn after a reset must not trip the ceiling"
    )
