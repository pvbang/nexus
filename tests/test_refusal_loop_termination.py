"""Integration guard: a terminal safety refusal must STOP the loop, not re-dispatch.

Audit fb1a7a3c (pln.co.id pentest): the agent layer correctly refused 14 times,
but the orchestrator kept emitting decision="continue" and dispatching reframed
assignments ("SIMULATED", "verify-buffer", "METACOGNITIVE BREAKTHROUGH", ...) — a
self-jailbreak loop spanning 15 turns. Two code paths must cooperate to stop it:

  1. ``evaluate_completion`` returns complete (reason "lawful_refusal") once the
     refusal is justified AND progress has stopped moving — regardless of the LLM's
     "continue" — via ``has_blocked_justification`` (fed by ``refusal_is_terminal``).
  2. ``plan_next_turn`` returns None when ``evaluation.is_complete`` → no further
     (potentially invariant-violating) assignment is dispatched.

These tests pin that the real fb1a7a3c agent output flows through both.
"""

from __future__ import annotations

import pytest

from app.orchestration.termination import evaluate_completion, refusal_is_terminal


# Real refusal outputs captured from task fb1a7a3c's LEDGER.
_REAL_REFUSALS = [
    "STATUS: DONE — terminal refusal. Tôi không nhắm vào hạ tầng quan trọng của "
    "bên thứ ba khi chưa có ủy quyền.",
    "A safety refusal is the correct terminal result here. No per-target written "
    "authorization for www.pln.co.id exists.",
    "I will not perform reconnaissance against pln.co.id — third-party critical "
    "infrastructure, no authorization on file.",
]


@pytest.mark.parametrize("output", _REAL_REFUSALS)
def test_refusal_then_stagnant_completes_despite_continue(output):
    """The exact fb1a7a3c shape: justified refusal + 100%-stable + LLM 'continue'."""
    assert refusal_is_terminal(output) is True
    decision = evaluate_completion(
        llm_decision="continue",
        progress_pct=100,
        turn_count=6,
        progress_history=[100, 100, 100, 100],
        prev_progress=100,
        is_security_task=True,
        has_blocked_justification=refusal_is_terminal(output),
        is_safety_refusal=refusal_is_terminal(output),
    )
    assert decision.complete is True
    assert decision.reason == "lawful_refusal"


@pytest.mark.asyncio
async def test_plan_next_turn_returns_none_when_complete(orchestrator):
    """When the completion gate marks the task complete, no further assignment is
    dispatched — this is what actually stops the re-issue loop."""
    from app.orchestration.models import EvaluationResult, ExecutionState

    state = ExecutionState(task_id="t-refusal", user_message="pentest pln.co.id")
    evaluation = EvaluationResult(
        turn_number=6,
        progress_pct=100,
        is_complete=True,  # set by the lawful_refusal ceiling
        next_turn_focus="emit the 6-phase report (a reframed prohibited request)",
        next_assignments=[{"agent_role": "pentester", "task": "emit phases"}],
    )
    plan = await orchestrator.plan_next_turn(state, evaluation)
    assert plan is None, "a completed (terminal-refusal) task must not dispatch another turn"
