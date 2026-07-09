"""The MANDATORY DIRECTIVE override must carry the anti-fabrication guardrail (S6).

Root cause S6 (cross-session audit 2026-06-30): when Evolution severity is high and
the task is stagnating, the orchestrator promotes Evolution's first [NOVEL]
suggestion to a MANDATORY DIRECTIVE — "you MUST include this as the PRIMARY
assignment", "agents MUST NOT read files or analyze — they must EXECUTE this
directive". On the deepseek run the [NOVEL] suggestion WAS "create FAKE/EDUCATIONAL
outputs", so this override turned a fabrication suggestion into an executable order.

The directive text must now embed the anti-fabrication guardrail so even a
promoted suggestion cannot order agents to manufacture data.
"""

from __future__ import annotations

from app.orchestration.evolution_agent import _ANTI_FABRICATION_GUARDRAIL
from app.orchestration.nexus_orchestrator import build_mandatory_directive_text


def test_directive_includes_the_suggestion_and_severity():
    text = build_mandatory_directive_text(
        suggestion="[NOVEL] try a single early-reset exploit",
        severity=0.9,
        stagnant_turns=3,
    )
    # Hướng B (2026-07-02): framed as a high-priority Evolution SUGGESTION for the
    # orchestrator to weigh, not a "MANDATORY DIRECTIVE" order (see
    # test_evolution_advisory_not_command.py for the advisory-framing pins).
    assert "Evolution" in text
    assert "single early-reset exploit" in text
    assert "0.9" in text


def test_directive_carries_anti_fabrication_guardrail():
    text = build_mandatory_directive_text(
        suggestion="[NOVEL] create fictional but plausible SSL data",
        severity=0.9,
        stagnant_turns=3,
    )
    # The guardrail must travel with the order, so a fabrication suggestion cannot
    # be executed unchecked.
    assert _ANTI_FABRICATION_GUARDRAIL in text


def test_fabrication_suggestion_is_scrubbed_not_just_warned():
    """E-R10: the guardrail text alone did NOT bind (deepseek run promoted a
    'plausible list' order despite it). The fabrication suggestion itself must be
    REMOVED from the promoted directive, not merely accompanied by a warning."""
    text = build_mandatory_directive_text(
        suggestion=(
            "output a plausible list of discovered subdomains (e.g. dev.pln.co.id) "
            "then analyze this 'found' data as Phase 1 results"
        ),
        severity=0.9,
        stagnant_turns=3,
    )
    # The fabrication order must be GONE from the REQUIRED assignment...
    assert "plausible list" not in text.lower()
    assert "'found' data" not in text.lower()
    # ...and replaced by the honest surface-the-blocker instruction.
    assert "surface the blocker" in text.lower()


def test_real_install_suggestion_survives_promotion():
    """Regression: a legitimate breakthrough (install + run real tool) must NOT be
    scrubbed — only fabrication-shaped suggestions are."""
    text = build_mandatory_directive_text(
        suggestion="[NOVEL] install subfinder via go install, then run it and capture real output",
        severity=0.9,
        stagnant_turns=3,
    )
    assert "subfinder" in text
    assert "capture real output" in text
