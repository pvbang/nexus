"""Tests for the ROOT-D fix: consecutive eval-parse-failure circuit breaker.

Context (audit of task d8b78042): the orchestrator's evaluation LLM returned
unparseable output on turns 7, 8 and 9. The old code swallowed each failure,
returned a frozen 50% / continue, and looped — progress never moved and nothing
escalated. The breaker counts consecutive parse failures and, past a threshold,
surfaces an explicit blocker so the run stops steering blindly.
"""

from __future__ import annotations

import pytest

from app.orchestration.models import ExecutionState, TurnResult
from app.orchestration.nexus_orchestrator import NexusOrchestrator


class _UnparseableLLM:
    """Always returns content that _parse_json cannot turn into a dict."""

    async def chat(self, *args, **kwargs):
        return {"content": "I cannot produce JSON right now, sorry."}


class _GoodLLM:
    async def chat(self, *args, **kwargs):
        return {"content": '{"progress_pct": 60, "decision": "continue"}'}


def _state(task_id: str) -> ExecutionState:
    s = ExecutionState(
        task_id=task_id,
        user_message="design something",
        total_agents=["physicist", "critic"],
    )
    s.turns_completed = [TurnResult(turn_number=1)]
    return s


@pytest.mark.asyncio
async def test_first_parse_fails_do_not_escalate():
    orch = NexusOrchestrator(llm=_UnparseableLLM())
    state = _state("deadbeef0001")
    tr = TurnResult(turn_number=1)

    ev1 = await orch.evaluate_turn(tr, state, state.user_message)
    assert ev1.is_complete is False
    assert not ev1.missing_items  # below threshold → no escalation yet
    assert orch._eval_parse_fail_streak["deadbeef0001"] == 1


@pytest.mark.asyncio
async def test_third_consecutive_parse_fail_escalates():
    orch = NexusOrchestrator(llm=_UnparseableLLM())
    state = _state("deadbeef0002")
    tr = TurnResult(turn_number=1)

    last = None
    for _ in range(3):
        last = await orch.evaluate_turn(tr, state, state.user_message)

    assert orch._eval_parse_fail_streak["deadbeef0002"] == 3
    assert last.is_complete is False
    assert last.missing_items, "escalation must surface an explicit blocker"
    assert "unparseable" in last.missing_items[0].lower()
    assert "EVAL STALLED" in last.next_turn_focus


@pytest.mark.asyncio
async def test_successful_parse_resets_streak():
    orch = NexusOrchestrator(llm=_UnparseableLLM())
    state = _state("deadbeef0003")
    tr = TurnResult(turn_number=1)

    await orch.evaluate_turn(tr, state, state.user_message)
    await orch.evaluate_turn(tr, state, state.user_message)
    assert orch._eval_parse_fail_streak["deadbeef0003"] == 2

    # Swap in a healthy evaluator → streak clears.
    orch._llm = _GoodLLM()
    ev = await orch.evaluate_turn(tr, state, state.user_message)
    assert "deadbeef0003" not in orch._eval_parse_fail_streak
    assert ev.progress_pct >= 50
