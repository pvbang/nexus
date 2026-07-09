"""Planner must expand a vague product request into a COMPLETE deliverable set.

Real users type "làm cho tôi website dạy tiếng anh" but expect a ready-to-use
product. The planner prompt must instruct expansion of implicit-but-required
completeness — while still (a) not creeping into a different product's scope and
(b) honoring an explicit narrowing ("chỉ..."). This is the design intent behind
the Completeness Mandate (2026-07-01)."""

from __future__ import annotations

from app.orchestration.prompts import build_orchestrator_planning_prompt


def _planning() -> str:
    return build_orchestrator_planning_prompt(agent_catalog="- developer\n- writer")


def test_planning_prompt_has_completeness_mandate():
    p = _planning()
    assert "COMPLETENESS MANDATE" in p
    # teaches expanding a short request into the full usable product
    assert "immediate real use" in p.lower() or "ready-to-use" in p.lower()


def test_completeness_mandate_states_the_scope_boundary():
    p = _planning()
    low = p.lower()
    # completeness is bounded by the product's own nature, not scope-creep
    assert "scope-creep" in low or "different product" in low
    # and an explicit user narrowing wins
    assert "chỉ" in p or "just fix" in low or "explicit" in low


def test_completeness_mandate_does_not_stall_for_clarification():
    # autonomous runs must infer the v1, not block asking the user
    low = _planning().lower()
    assert "do not stall" in low or "infer the complete" in low


def test_mandate_excludes_microtasks_from_expansion():
    # Generalization guard: a translation / question / single email / code review /
    # quick fix must NOT be auto-expanded into extra artifacts (over-fire risk the
    # dry-run found). The boundary must explicitly carve out microtasks.
    p = _planning()
    low = p.lower()
    assert "not every request is a" in low or "microtask" in low
    # names concrete microtask kinds so a weak model recognizes them
    assert "translation" in low and "code review" in low
    # and states completeness for these = the answer itself, not new artifacts
    assert "do not manufacture extra artifacts" in low or "not that you invented deliverables" in low
