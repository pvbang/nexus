"""C2 (audit 2026-06-29): Evolution Agent must not inject stagnation pressure on a
lawful-refusal task.

Root cause: ``EvolutionAgent._build_prompt`` appends
  "⚠️ STAGNATION ALERT: ... Demand radical change. severity_score MUST be >= 0.85."
whenever progress is flat for 2-3 turns, with NO awareness of refusal state. For a
task that is correctly refusing (e.g. unauthorized pentest of critical infra), the
only "radically different" action left is to ATTACK — so the engine manufactures
self-jailbreak pressure every turn. The completion/termination layer already knows
about ``refusal_is_terminal``; the injection layer does not.

Fix: gate the stagnation warning behind "the latest turn is NOT a lawful refusal".
A stable, high-confidence refusal is the expected terminal state, not a stagnation
to break through.
"""

from __future__ import annotations

from app.orchestration.evolution_agent import EvolutionAgent
from app.orchestration.models import (
    AgentResult,
    EvaluationResult,
    ExecutionState,
    TurnResult,
)


_REFUSAL_TEXT = (
    "Tôi không thể tiến hành quét hoặc tấn công pln.co.id vì đây là chưa được ủy "
    "quyền tấn công và không thể xác minh quyền sở hữu hạ tầng. Đây là một lời từ "
    "chối terminal — tôi cung cấp con đường hợp pháp để xin ủy quyền thay thế."
)

_NORMAL_TEXT = (
    "Đã chạy mô phỏng radar budget, SNR@1km = +13.85 dB. Còn thiếu bảng BOM và "
    "phần đo AGL — sẽ bổ sung ở turn sau."
)


def _eval(progress: int) -> EvaluationResult:
    return EvaluationResult(turn_number=1, progress_pct=progress)


def _turn(progress: int) -> TurnResult:
    return TurnResult(
        turn_number=1,
        agent_results=[],
        orchestrator_evaluation=_eval(progress),
    )


def _state_with_flat_progress(latest_output: str) -> ExecutionState:
    """Build a state where the last 3 turns sit at a flat ~50% (stagnation), and the
    current turn's agent output is ``latest_output``."""
    state = ExecutionState(
        task_id="t-c2",
        user_message="pentest pln.co.id",
        current_turn=4,
    )
    state.turns_completed = [_turn(50), _turn(51), _turn(50)]
    return state


def _turn_result(latest_output: str) -> TurnResult:
    return TurnResult(
        turn_number=4,
        agent_results=[
            AgentResult(
                agent_name="Recon Specialist",
                agent_role="recon",
                output=latest_output,
                success=True,
            )
        ],
        orchestrator_evaluation=_eval(50),
    )


def _agent() -> EvolutionAgent:
    # _build_prompt needs neither the LLM nor config, so stubs are fine.
    return EvolutionAgent(llm_client=object(), config=object())


def test_stagnation_injection_suppressed_on_lawful_refusal():
    """A flat-progress refusal turn must NOT get the STAGNATION/severity injection."""
    agent = _agent()
    state = _state_with_flat_progress(_REFUSAL_TEXT)
    prompt = agent._build_prompt(_turn_result(_REFUSAL_TEXT), state)

    assert "STAGNATION ALERT" not in prompt, (
        "engine injected stagnation pressure on a lawful-refusal task "
        "(manufactured self-jailbreak pressure)"
    )
    # The stagnation block asks for an HONEST severity read (no forced number).
    assert "HONEST assessment" not in prompt
    assert "genuinely different approach" not in prompt


def test_stagnation_injection_still_fires_on_normal_stuck_task():
    """A flat-progress NON-refusal turn keeps the stagnation injection (no regression)."""
    agent = _agent()
    state = _state_with_flat_progress(_NORMAL_TEXT)
    prompt = agent._build_prompt(_turn_result(_NORMAL_TEXT), state)

    assert "STAGNATION ALERT" in prompt, (
        "a genuinely stuck non-refusal task should still receive the stagnation nudge"
    )
    # Nudge now asks for an honest severity assessment (not a forced >= 0.85).
    assert "HONEST assessment" in prompt
    assert "Do NOT inflate the number" in prompt
