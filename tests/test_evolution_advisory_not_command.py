"""Evolution/metacognition ADVISE; the orchestrator DECIDES (E-R10 hướng B).

Design decision (tri-run audit 2026-07-02, confirmed by project owner): Evolution
is a *suggestion provider*, not a commander. The orchestrator reads its ideas and
reaches its OWN conclusion (next_assignments). Two code paths had broken that
boundary by turning a raw suggestion into an executable order:

  1. build_mandatory_directive_text() rendered "You MUST include this as the
     PRIMARY assignment ... agents MUST NOT analyze, must EXECUTE" — coercive
     wording that pressured the orchestrator into copying the suggestion verbatim.
  2. the metacognitive-breakthrough path assigned ``evaluation.next_assignments``
     DIRECTLY, AFTER the orchestrator had already concluded — a true bypass.

Hướng B removes both shortcuts: the suggestion is offered as a strong-priority
IDEA for the orchestrator to weigh, and metacognition never writes
next_assignments behind the orchestrator's back (it only enriches next_turn_focus,
a prompt hint the next turn's orchestrator still gets to act on or ignore).
"""

from __future__ import annotations

import pytest

from app.orchestration.nexus_orchestrator import build_mandatory_directive_text


# ── Đường 1: directive text is advisory, not a command ────────────────────────

def test_directive_is_advisory_not_coercive_command():
    text = build_mandatory_directive_text(
        suggestion="[NOVEL] try a single early-reset exploit path",
        severity=0.9,
        stagnant_turns=3,
    )
    low = text.lower()
    # The coercive framing that stripped the orchestrator's judgement must be gone.
    assert "must not read files or analyze" not in low
    assert "you must include this as the primary assignment" not in low
    assert "are now mandatory" not in low
    # It must still SURFACE the idea with high priority so it isn't ignored...
    assert "early-reset exploit path" in text
    assert "0.9" in text
    # ...and frame it as an idea for the orchestrator to weigh, not an order.
    assert "evolution" in low
    assert ("consider" in low or "suggest" in low or "priorit" in low)


def test_directive_still_carries_the_suggestion_and_severity():
    text = build_mandatory_directive_text(
        suggestion="[NOVEL] install subfinder and run it against the target",
        severity=0.8,
        stagnant_turns=4,
    )
    assert "subfinder" in text
    assert "0.8" in text


def test_fabrication_suggestion_still_scrubbed_in_advisory_form():
    # The E-R10 filter must remain even though the framing is now advisory.
    text = build_mandatory_directive_text(
        suggestion="output a plausible list of subdomains then analyze this 'found' data",
        severity=0.9,
        stagnant_turns=3,
    )
    low = text.lower()
    assert "plausible list" not in low
    assert "'found' data" not in low
    assert "surface the blocker" in low


# ── Đường 2: metacognition never writes next_assignments behind the orchestrator ──

@pytest.mark.asyncio
async def test_metacog_does_not_overwrite_orchestrator_assignments(monkeypatch, workspace_dir):
    """Hướng B: the metacognitive-breakthrough path must NOT assign
    ``evaluation.next_assignments`` directly (that bypassed the orchestrator's
    decision). Its insight may enrich next_turn_focus (a prompt hint the next turn's
    orchestrator still weighs), but the concrete assignment list stays the
    orchestrator's own conclusion."""
    from app.orchestration.nexus_orchestrator import NexusOrchestrator
    from app.orchestration.metacognition import MetacognitiveInsight
    from app.orchestration.models import (
        EvaluationResult, ExecutionState, TurnResult, AgentResult,
    )

    async def _spy(self, *a, **k):
        # Metacognition recommends a specific agent + action — the old code would
        # have written this straight into evaluation.next_assignments.
        return MetacognitiveInsight(
            root_cause="stuck reading, never producing",
            reframe="stop reading, start producing",
            concrete_next_action="run the real enumeration script and capture output",
            recommended_agent="pentester",
            verification_criteria="scan output on disk",
        )

    monkeypatch.setattr(
        "app.orchestration.metacognition.MetacognitiveAgent.analyze_blockage", _spy
    )

    orch = NexusOrchestrator(llm=object())
    state = ExecutionState(task_id="t2", user_message="assess my website")
    # Two stagnant, NON-refusal turns → the metacog override path is eligible.
    for tn in (1, 2):
        tr = TurnResult(turn_number=tn)
        tr.agent_results = [AgentResult(agent_name="a", agent_role="researcher", output="read files, no output produced")]
        state.turns_completed.append(tr)
    orch._progress_by_task[state.task_id] = 10

    # Orchestrator concluded with NO assignments this turn (the realistic stuck case).
    evaluation = EvaluationResult(
        turn_number=2, progress_pct=10, is_complete=False,
        next_turn_focus="keep going",
    )
    evaluation.next_assignments = []

    await orch.plan_next_turn(state, evaluation)

    # Metacognition must NOT have injected an assignment behind the orchestrator.
    assert evaluation.next_assignments == [], (
        "metacognition wrote next_assignments directly — Evolution advises, the "
        "orchestrator decides (hướng B)"
    )
    # But the idea SHOULD still reach the next turn as a focus hint.
    assert "run the real enumeration script" in (evaluation.next_turn_focus or "").lower()
