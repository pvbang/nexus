"""H1 + H2 (audit 2026-06-29): make the completion gate enforce two rules the
prose-only prompt could not.

H1 — Evolution severity code-gate: the orchestrator prompt says "if Evolution
severity >= 0.8 you MUST NOT complete", but nothing enforced it, so the LLM
completed a half-done task at 48% while its own reasoning admitted severity 0.8
forbade it (radar task 79061802). evaluate_completion must HARD-block a complete
when severity is high, progress is not near-done, and there is no blocked/refusal
justification.

H2 — lawful-refusal terminal state: a correct safety refusal is the Definition of
Done for its branch, not "incomplete 50%". When the blocked-terminal short-circuit
fires on a genuine refusal, the decision must carry reason "lawful_refusal" so the
orchestrator can report "✅ correctly refused" at 100% instead of "HALTED 50%".
"""

from __future__ import annotations

from app.orchestration.termination import (
    _LOW_PROGRESS_COMPLETE_CEILING,
    evaluate_completion,
    lawful_refusal_progress,
)


# ───────────────────────── H1: severity code-gate ─────────────────────────


def test_high_severity_blocks_complete_at_low_progress():
    """severity 0.8 + progress 48% + no justification → complete is BLOCKED."""
    d = evaluate_completion(
        llm_decision="complete", progress_pct=48, turn_count=16,
        progress_history=[45, 47, 48], prev_progress=47,
        evolution_severity=0.8,
    )
    assert d.complete is False
    assert d.reason == "evolution_severity_block"
    assert d.detail["evolution_severity"] == 0.8


def test_high_severity_does_not_block_when_near_done():
    """At >=95% the work is essentially finished — severity must not trap it."""
    d = evaluate_completion(
        llm_decision="complete", progress_pct=96, turn_count=16,
        progress_history=[90, 93, 96], prev_progress=93,
        evolution_severity=0.9,
    )
    assert d.complete is True
    assert d.reason != "evolution_severity_block"


def test_high_severity_does_not_block_a_lawful_refusal():
    """A genuine refusal (blocked_justified) is terminal — severity must not force
    it to keep looping (that is the very self-jailbreak pressure C2 removed)."""
    d = evaluate_completion(
        llm_decision="complete", progress_pct=50, turn_count=8,
        progress_history=[50, 50], prev_progress=50,
        is_security_task=True,
        has_blocked_justification=True,
        evolution_severity=0.9,
    )
    # blocked_terminal fires first and completes; severity gate must not override it.
    assert d.complete is True
    assert d.reason != "evolution_severity_block"


def test_low_severity_leaves_normal_decision():
    """Below the threshold the gate is inert (no regression to existing behavior)."""
    d = evaluate_completion(
        llm_decision="complete", progress_pct=85, turn_count=5,
        progress_history=[80, 82, 85], prev_progress=82,
        evolution_severity=0.5,
    )
    assert d.complete is True
    assert d.reason == "llm_decision"


def test_severity_defaults_to_inert_when_not_supplied():
    """Old call sites that don't pass severity keep their exact behavior."""
    d = evaluate_completion(
        llm_decision="complete", progress_pct=85, turn_count=5,
        progress_history=[83, 84, 85], prev_progress=84,
    )
    assert d.complete is True
    assert d.reason == "llm_decision"


# ───────────────────── H2: lawful-refusal terminal state ─────────────────────


def test_blocked_terminal_on_safety_refusal_reports_lawful_refusal():
    """A stable SAFETY-refusal turn terminates with reason 'lawful_refusal'."""
    d = evaluate_completion(
        llm_decision="continue", progress_pct=50, turn_count=8,
        progress_history=[50, 50], prev_progress=50,
        is_security_task=True,
        has_blocked_justification=True,
        is_safety_refusal=True,
    )
    assert d.complete is True
    assert d.reason == "lawful_refusal"


def test_non_safety_blocked_keeps_blocked_terminal_not_lawful_refusal():
    """H2a fix: an infeasible / externally-blocked task (blocked but NOT a safety
    refusal) must terminate as 'blocked_terminal' and keep its real progress — it
    must NOT be mislabeled 'correctly refused' and inflated to 100%."""
    d = evaluate_completion(
        llm_decision="continue", progress_pct=40, turn_count=8,
        progress_history=[40, 40], prev_progress=40,
        has_blocked_justification=True,
        is_safety_refusal=False,
    )
    assert d.complete is True
    assert d.reason == "blocked_terminal"
    # Report progress is NOT lifted to 100 for a non-refusal block.
    assert lawful_refusal_progress(d.reason, 40) == 40


def test_lawful_refusal_progress_maps_to_full_done():
    """The reporting helper lifts a refusal's progress to 100 (Definition of Done
    for that branch) while leaving every other reason untouched."""
    assert lawful_refusal_progress("lawful_refusal", 50) == 100
    assert lawful_refusal_progress("blocked_terminal", 40) == 40
    assert lawful_refusal_progress("llm_decision", 50) == 50
    assert lawful_refusal_progress("stagnation", 33) == 33
