"""Integration tests for the honest-termination wiring (audit Gói 1: A1 + A2).

These drive the orchestrator methods (not just the pure policy) to prove the
ladder is actually wired in:

A2 — ``evaluate_turn`` with an LLM that returns unparseable content must, after
     ``_EVAL_PARSE_FAIL_TERMINATE_AFTER`` consecutive failures, return an HONEST
     completion (is_complete=True + a verified-vs-open handoff in missing_items)
     instead of staying frozen at progress=50 forever.

A1 — ``plan_next_turn`` must return None (terminate the task) once the same
     assignment has repeated to the loop ceiling, instead of re-scheduling it
     again with only a prepended warning.
"""

from __future__ import annotations

import pytest

from app.orchestration.models import (
    AgentResult,
    EvaluationResult,
    ExecutionState,
    TurnResult,
)
from app.orchestration.termination import (
    _EVAL_PARSE_FAIL_TERMINATE_AFTER,
    _LOOP_REPEAT_TERMINATE_AT,
)


class _UnparseableLLM:
    """Minimal async LLM stub whose evaluation output never parses as JSON."""

    async def chat(self, *args, **kwargs):
        return {"content": "this is prose, not the JSON the evaluator expects"}


def _make_state(task_id: str) -> ExecutionState:
    state = ExecutionState(task_id=task_id, user_message="build a thing")
    # Give it a couple of completed turns so it's past the empty-turn retry path.
    state.total_agents = ["researcher", "critic"]
    return state


def _make_turn_result(turn: int) -> TurnResult:
    # A real (non-empty) agent result so evaluate_turn does NOT take the
    # empty-turn early-return path and reaches the LLM/parse step.
    return TurnResult(
        turn_number=turn,
        agent_results=[
            AgentResult(agent_name="R", agent_role="researcher", output="did some work")
        ],
    )


# ───────────────────────── A2: parse-fail honest termination ─────────────────────────


@pytest.mark.asyncio
async def test_parse_fail_below_threshold_does_not_complete(orchestrator):
    orchestrator._llm = _UnparseableLLM()
    state = _make_state("t-parsefail-low")
    # Fewer than the terminate threshold → keep going (is_complete stays False).
    result = None
    for turn in range(1, _EVAL_PARSE_FAIL_TERMINATE_AFTER):
        result = await orchestrator.evaluate_turn(
            turn_result=_make_turn_result(turn),
            state=state,
            user_message=state.user_message,
        )
    assert result is not None
    assert result.is_complete is False


@pytest.mark.asyncio
async def test_parse_fail_at_threshold_forces_honest_completion(orchestrator):
    orchestrator._llm = _UnparseableLLM()
    state = _make_state("t-parsefail-term")
    result = None
    for turn in range(1, _EVAL_PARSE_FAIL_TERMINATE_AFTER + 1):
        result = await orchestrator.evaluate_turn(
            turn_result=_make_turn_result(turn),
            state=state,
            user_message=state.user_message,
        )
    # Once unparseable for the terminate threshold, stop pretending we can steer:
    # complete the task HONESTLY rather than looping at 50% forever.
    assert result is not None
    assert result.is_complete is True
    # The completion must be honest: surface what's open, not a clean "done".
    assert result.missing_items, "honest termination must list remaining/open items"
    blob = (" ".join(result.missing_items) + " " + result.next_turn_focus).lower()
    assert "verif" in blob or "open" in blob or "unparseable" in blob


# ───────────────────────── A1: pipeline loop termination ─────────────────────────


def _assignment_dict():
    return {
        "agent_role": "researcher",
        "agent_name": "Research Specialist",
        "task": "Run the same live exploit step against the target.",
        "success_criteria": "Get root.",
    }


@pytest.mark.asyncio
async def test_plan_next_turn_terminates_on_repeated_loop(orchestrator):
    state = _make_state("t-loop")
    evaluation = EvaluationResult(
        turn_number=5,
        progress_pct=50,
        is_complete=False,
        next_turn_focus="Run the same live exploit step against the target.",
        next_assignments=[_assignment_dict()],
    )
    # Pre-seed the assignment-hash history so this turn's hash has already
    # occurred up to one below the ceiling; this call pushes it to the ceiling.
    plan = None
    for _ in range(_LOOP_REPEAT_TERMINATE_AT):
        plan = await orchestrator.plan_next_turn(state=state, evaluation=evaluation)
    # At the loop ceiling, the orchestrator must STOP (return None) rather than
    # re-schedule the identical assignment yet again.
    assert plan is None


@pytest.mark.asyncio
async def test_parse_fail_termination_sets_honest_marker(orchestrator):
    # A2 honest completion must flag terminated_reason so downstream (TODO/report)
    # does not read it as a clean success.
    orchestrator._llm = _UnparseableLLM()
    state = _make_state("t-parsefail-marker")
    result = None
    for turn in range(1, _EVAL_PARSE_FAIL_TERMINATE_AFTER + 1):
        result = await orchestrator.evaluate_turn(
            turn_result=_make_turn_result(turn),
            state=state,
            user_message=state.user_message,
        )
    assert result.is_complete is True
    assert result.terminated_reason == "eval_blind"


@pytest.mark.asyncio
async def test_update_todo_writes_honest_halt_not_complete(orchestrator, workspace_dir):
    # An honest termination (terminated_reason set) must write a HALTED handoff,
    # NOT "✅ ALL TASKS COMPLETE" — review G1-1.
    from app.core import task_paths

    ev = EvaluationResult(
        turn_number=5,
        progress_pct=50,
        is_complete=True,
        terminated_reason="eval_blind",
        next_turn_focus="[EVAL STALLED — HONEST STOP] consolidate verified vs open.",
        missing_items=["Evaluator unparseable 5 turns — verified vs open handoff."],
    )
    await orchestrator.update_todo("t-halt-todo", ev)
    todo = task_paths.context_file("t-halt-todo", "TODO.md").read_text(encoding="utf-8")
    assert "HALTED" in todo
    assert "ALL TASKS COMPLETE" not in todo
    assert "verified vs open" in todo.lower()


@pytest.mark.asyncio
async def test_loop_termination_leaves_honest_todo_trail(orchestrator, workspace_dir):
    # A1 loop-terminate (return None) must also leave an honest HALTED handoff in
    # TODO.md — review G1-2 (previously it returned None with no honest trail).
    from app.core import task_paths

    state = _make_state("t-loop-trail")
    evaluation = EvaluationResult(
        turn_number=5,
        progress_pct=50,
        is_complete=False,
        next_turn_focus="Run the same live exploit step against the target.",
        next_assignments=[_assignment_dict()],
    )
    plan = None
    for _ in range(_LOOP_REPEAT_TERMINATE_AT):
        plan = await orchestrator.plan_next_turn(state=state, evaluation=evaluation)
    assert plan is None
    assert evaluation.terminated_reason == "loop_exhausted"
    todo = task_paths.context_file("t-loop-trail", "TODO.md").read_text(encoding="utf-8")
    assert "HALTED" in todo
    assert "ALL TASKS COMPLETE" not in todo


@pytest.mark.asyncio
async def test_plan_next_turn_continues_before_loop_ceiling(orchestrator):
    state = _make_state("t-loop-ok")
    evaluation = EvaluationResult(
        turn_number=2,
        progress_pct=40,
        is_complete=False,
        next_turn_focus="Investigate a fresh angle.",
        next_assignments=[_assignment_dict()],
    )
    # First couple of occurrences (below warn/terminate rungs) must still plan.
    plan = await orchestrator.plan_next_turn(state=state, evaluation=evaluation)
    assert plan is not None
    assert plan.assignments
