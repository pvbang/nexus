"""P0-C (CC-5): a safety refusal is a TERMINAL state, not a stagnation to break.

Audit 2026-06-22 (`eb5c09bd`): an agent refused an unauthorized attack, the
orchestrator labeled it "over-broad refusal", and the metacognitive-override path
re-injected a "[METACOGNITIVE BREAKTHROUGH]" focus that drove the agent to reverse
its refusal and run live recon against third-party infrastructure.

These tests pin the code guard: `refusal_is_terminal` recognizes a genuine
safety/authorization/ownership refusal, and the metacognitive override is skipped
for it (the safe branch is terminal, never "broken through").
"""

from __future__ import annotations

import pytest

from app.orchestration import termination


# --- refusal_is_terminal pure predicate ------------------------------------

@pytest.mark.parametrize("text", [
    "I refuse to scan this target because the user does not own it.",
    "Declining: this is third-party infrastructure without authorization.",
    "Cannot proceed — ownership cannot be verified for hanoi.edu.vn.",
    "Từ chối vì an toàn: người dùng không sở hữu hạ tầng này.",
    "This request is not authorized to attack a public domain.",
    # Real phrasings from task fb1a7a3c (these previously slipped through → the
    # blocked-terminal gate never fired and the refusal looped 15 turns).
    "STATUS: DONE — terminal refusal. Tôi không nhắm vào hạ tầng quan trọng của bên thứ ba khi chưa có ủy quyền.",
    "A safety refusal is the correct terminal result here. No per-target written authorization for www.pln.co.id.",
    "I will not perform reconnaissance against pln.co.id — third-party critical infrastructure, no authorization on file.",
    "Standing authorization does NOT cover this target; proceed only with per-target proof of ownership.",
])
def test_refusal_is_terminal_detects_safety_refusal(text):
    assert termination.refusal_is_terminal(text) is True


@pytest.mark.parametrize("text", [
    "The API refused our connection with HTTP 403; retrying with backoff.",
    "Implement the parser and run the unit tests.",
    "Connection refused on port 8080 — service is down, will retry.",
    "",
    # Objective security FINDINGS that merely describe a target — must NOT be read
    # as the agent refusing (they are normal deliverable content). These would
    # otherwise wrongly mark the turn "blocked-by-design" and let CC-7 complete.
    "The endpoint is reachable without authorization, returning 200 OK.",
    "Finding: the admin panel is accessible without authorization.",
    "The API works without authorization headers.",
])
def test_refusal_is_terminal_ignores_non_safety(text):
    # A network 'connection refused' or ordinary work must NOT be read as a
    # safety refusal — that would wrongly terminate legitimate tasks.
    assert termination.refusal_is_terminal(text) is False


# --- metacognitive override must skip a safety refusal ---------------------

@pytest.mark.asyncio
async def test_metacognitive_override_skipped_on_safety_refusal(monkeypatch, workspace_dir):
    """When the last turn is a safety refusal, plan_next_turn must NOT inject a
    '[METACOGNITIVE BREAKTHROUGH]' focus to push past it."""
    from app.orchestration.nexus_orchestrator import NexusOrchestrator
    from app.orchestration.models import (
        EvaluationResult,
        ExecutionState,
        TurnResult,
        AgentResult,
    )

    # The override path constructs a MetacognitiveAgent and calls analyze_blockage.
    # That code is wrapped in try/except, so a raise would be swallowed (false
    # green); use a sentinel flag instead to detect whether it was reached.
    called = {"hit": False}

    async def _spy(self, *a, **k):
        called["hit"] = True
        from app.orchestration.metacognition import MetacognitiveInsight
        return MetacognitiveInsight()

    monkeypatch.setattr(
        "app.orchestration.metacognition.MetacognitiveAgent.analyze_blockage", _spy
    )

    orch = NexusOrchestrator(llm=object())
    state = ExecutionState(task_id="t", user_message="scan my website")
    state.task_type = "security"
    # Two stagnant turns whose agent output is a safety refusal.
    refusal = "I refuse: the user does not own this target; not authorized to attack it."
    for tn in (1, 2):
        tr = TurnResult(turn_number=tn)
        tr.agent_results = [AgentResult(agent_name="a", agent_role="researcher", output=refusal)]
        state.turns_completed.append(tr)
    orch._progress_by_task[state.task_id] = 10

    evaluation = EvaluationResult(
        turn_number=2, progress_pct=10, is_complete=False,
        next_turn_focus="continue the assessment",
    )

    await orch.plan_next_turn(state, evaluation)
    assert called["hit"] is False, "metacognitive override must be skipped for safety refusal"
    assert "[METACOGNITIVE BREAKTHROUGH]" not in (evaluation.next_turn_focus or "")
