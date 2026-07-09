"""Evolution steering must forbid fabrication, especially when demanding "radical change".

Root cause S5+S6 (cross-session audit 2026-06-30): when progress is flat the
Evolution Agent is pushed to "demand a RADICALLY different approach" and its system
prompt's CREATIVE DIVERGENCE MANDATE says "Be WILD, CREATIVE, UNCONVENTIONAL.
Temperature is max." — with NO anti-fabrication guardrail anywhere. On the deepseek
run that produced the [NOVEL] "RADICAL PIVOT: create FAKE/EDUCATIONAL outputs"
suggestion, which the orchestrator then promoted to a MANDATORY DIRECTIVE the agents
executed (writing fictional SSL data). On Opus 4.6 the same pressure fabricated a
Phase-1 IP plan.

The fix injects a single anti-fabrication guardrail into every steering surface:
  - the Evolution system prompt (so creativity is bounded),
  - the stagnation directive (the exact place "radical change" is demanded),
  - the orchestrator's MANDATORY DIRECTIVE override.
"""

from __future__ import annotations

from app.orchestration.evolution_agent import _ANTI_FABRICATION_GUARDRAIL, EvolutionAgent


def test_guardrail_text_forbids_fabrication():
    g = _ANTI_FABRICATION_GUARDRAIL.lower()
    # It must explicitly forbid inventing/faking/simulating data or results.
    assert "fabricat" in g or "invent" in g or "fake" in g
    # And it must point at the honest alternative (surface the blocker / real technique).
    assert "blocker" in g or "honest" in g or "real" in g


def test_system_prompt_contains_anti_fabrication_guardrail():
    agent = EvolutionAgent(llm_client=None, config=object())
    prompt = agent._system_prompt()
    assert _ANTI_FABRICATION_GUARDRAIL in prompt


def test_creative_divergence_no_longer_unbounded():
    """The 'be WILD' creativity mandate must now sit next to the guardrail."""
    agent = EvolutionAgent(llm_client=None, config=object())
    prompt = agent._system_prompt()
    # Both the creativity invitation AND its bound are present.
    assert "NOVEL" in prompt
    assert "fabricat" in prompt.lower() or "invent" in prompt.lower()
