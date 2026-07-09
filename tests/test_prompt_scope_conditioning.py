"""Tests for task-scope conditioning of the agent execution prompt.

Root cause being fixed: the execution prompt unconditionally pushed
"always full-stack / single-file NEVER acceptable / over-deliver on every
assignment" into EVERY agent on EVERY task — even simple tasks and
research/analysis tasks where that is over-engineering. The planner already
produces `complexity` and a deliverable `mode`, but that signal never reached
the prompt. These tests pin the conditioning behaviour.

Integrity directives (anti-fabrication, verification) must remain ALWAYS-ON
regardless of scope — they are quality, not over-engineering.
"""

from app.orchestration.prompts import (
    build_agent_execution_prompt,
    select_scope_guidance,
)

_FULLSTACK_MARKER = "single-file solution is NEVER acceptable"
_ANTIFAB_MARKER = "ANTI-FABRICATION"


def _exec(complexity: str, mode: str) -> str:
    return build_agent_execution_prompt(
        agent_name="A",
        agent_role="developer",
        personality="pragmatic",
        expertise=["python"],
        thinking_style="empirical",
        assignment="do the thing",
        success_criteria="it works",
        shared_context="",
        own_thinking="",
        previous_turns="",
        tool_catalog="",
        complexity=complexity,
        deliverable_mode=mode,
    )


def test_simple_task_drops_fullstack_push():
    """A simple task must NOT be told to build a full-stack project."""
    p = _exec("simple", "BUILD")
    assert _FULLSTACK_MARKER not in p
    assert "over-engineer" in p.lower()


def test_research_mode_drops_fullstack_push():
    """RESEARCH/ANALYZE deliverable is a report — not an app scaffold."""
    p = _exec("complex", "RESEARCH")
    assert _FULLSTACK_MARKER not in p
    assert "do not build apps" in p.lower()


def test_build_complex_keeps_production_push():
    """A genuine complex BUILD task SHOULD still get the production-grade push."""
    p = _exec("complex", "BUILD")
    assert _FULLSTACK_MARKER in p


def test_integrity_always_present_regardless_of_scope():
    """Anti-fabrication / verification doctrine is quality, never scoped out."""
    for complexity, mode in [
        ("simple", "BUILD"),
        ("complex", "RESEARCH"),
        ("medium", "MIXED"),
        ("expert", "BUILD"),
    ]:
        p = _exec(complexity, mode)
        assert _ANTIFAB_MARKER in p, f"integrity missing for {complexity}/{mode}"


def test_default_args_preserve_build_behaviour():
    """Back-compat: omitting the new args must keep the full production push."""
    p = build_agent_execution_prompt(
        agent_name="A",
        agent_role="developer",
        personality="p",
        expertise=["x"],
        thinking_style="t",
        assignment="do",
        success_criteria="works",
        shared_context="",
        own_thinking="",
        previous_turns="",
        tool_catalog="",
    )
    assert _FULLSTACK_MARKER in p
    assert _ANTIFAB_MARKER in p


def test_select_scope_guidance_routing():
    assert "over-engineer" in select_scope_guidance("simple", "BUILD").lower()
    assert "do not build apps" in select_scope_guidance("medium", "RESEARCH").lower()
    assert _FULLSTACK_MARKER in select_scope_guidance("complex", "BUILD")
    # Unknown/empty values fall back to the safe production default.
    assert _FULLSTACK_MARKER in select_scope_guidance("", "")
