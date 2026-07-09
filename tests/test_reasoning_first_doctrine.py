"""Tests for the Reasoning-First Doctrine (search-over-reliance fix).

Root cause being pinned: the prompt system taught a one-sided epistemology where
only tool/search output counted as "evidence" and internal reasoning was framed
as "guessing". Agents therefore treated search results as the answer instead of
as raw material to reason over. The fix injects a single Reasoning-First Doctrine
into BOTH prompt pipelines and rebalances the evidence/grading/judge language.

These tests guard that the doctrine reaches every tool-using agent in both the
single-agent (PromptBuilder) and multi-agent (orchestration) paths, and that the
old "reason == guess" / "data-rich == done" framing does not creep back.
"""

from __future__ import annotations

from app.core.prompt_builder import get_prompt_builder
from app.core.prompt_sections.behaviors import get_behaviors_section
from app.core.prompt_sections.reasoning_doctrine import REASONING_FIRST_DOCTRINE
from app.orchestration.prompts import (
    AGENT_EXECUTION_PRODUCTION_MANDATE,
    build_agent_thinking_prompt,
)

_MARKER = "REASONING-FIRST DOCTRINE"


def test_doctrine_in_single_agent_prompt():
    # Every tool-using agent in the single-agent path gets the doctrine.
    pb = get_prompt_builder()
    for role in ("core", "researcher", "developer", "analyst"):
        sp = pb.build_system_prompt(agent_type=role)
        assert _MARKER in sp, f"{role} single-agent prompt lost the doctrine"


def test_doctrine_in_orchestration_paths():
    # Multi-agent execution mandate + thinking prompt both carry the doctrine.
    assert _MARKER in AGENT_EXECUTION_PRODUCTION_MANDATE
    tp = build_agent_thinking_prompt(
        "Agent", "researcher", "curious", ["research"], "analytical",
        "investigate X", "answer X", "", "",
    )
    assert _MARKER in tp


def test_doctrine_frames_search_as_input_not_answer():
    # The core message must be present, not just the heading.
    d = REASONING_FIRST_DOCTRINE
    assert "search is an input, not the answer" in d.lower()
    assert "reason-from-knowledge" in d.lower()
    assert "lookup-required" in d.lower()
    # Must demand synthesis over relaying.
    assert "SYNTHESISE" in d or "synthesise" in d.lower()


def test_judge_rewards_synthesis_not_url_count():
    # The judge criteria must value synthesis and must not reward raw retrieval.
    judge = get_behaviors_section("judge")
    assert "Synthesis Quality" in judge
    assert "URL count is NOT quality" in judge
    # The old "resolved when evidence-rich; continue when data lacking" rule is gone.
    assert "'resolved' when evidence-rich" not in judge


def test_reason_is_not_equated_with_guess():
    # objective + core behaviors must no longer brand reasoning as guessing.
    from app.core.prompt_sections.objective import get_objective_section

    obj = get_objective_section()
    assert "don't guess when you can verify" not in obj
    assert "is NOT guessing" in obj or "not guessing" in obj.lower()

    core = get_behaviors_section("core")
    # The hard "minimum 2 tool calls per non-trivial task" quota is removed.
    assert "Minimum 2 tool calls" not in core


def test_evidence_definition_includes_derivation():
    # OUTPUT VALIDATION must accept a sound derivation as evidence for analytical claims.
    mandate = AGENT_EXECUTION_PRODUCTION_MANDATE
    assert "ANALYTICAL claims" in mandate
    assert "EMPIRICAL claims" in mandate
    # Derivation/computation explicitly counts as evidence.
    assert "derivation" in mandate.lower() or "Derived" in mandate
