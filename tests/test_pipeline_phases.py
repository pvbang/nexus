"""The orchestrator pipeline's phase order is declared explicitly and testable.

First slice of the god-object decomposition: the phase sequence that lives
implicitly inside NexusPipeline.run is mirrored as an explicit, ordered registry
built on MiddlewareChain. This pins the order under test and gives later slices a
seam to move real logic into, one phase at a time.
"""

from app.orchestration.pipeline_phases import build_pipeline_phases, PHASE_NAMES


def test_phases_in_expected_order():
    chain = build_pipeline_phases()
    resolved = chain.resolved()
    # Planning must come before execution which must come before final report.
    assert resolved.index("planning") < resolved.index("execution")
    assert resolved.index("execution") < resolved.index("final_report")


def test_all_core_phases_present():
    chain = build_pipeline_phases()
    resolved = set(chain.resolved())
    for required in ("context_aggregation", "planning", "execution",
                     "final_report", "summarize"):
        assert required in resolved


def test_phase_names_constant_matches_default_chain():
    chain = build_pipeline_phases()
    assert chain.resolved() == list(PHASE_NAMES)


def test_divergent_thinking_can_be_disabled():
    # Feature-gated phase: off → absent, on → positioned before execution.
    off = build_pipeline_phases(divergent_thinking=False).resolved()
    assert "divergent_thinking" not in off

    on = build_pipeline_phases(divergent_thinking=True).resolved()
    assert "divergent_thinking" in on
    assert on.index("divergent_thinking") < on.index("execution")


def test_evolution_phase_can_be_disabled():
    off = build_pipeline_phases(evolution=False).resolved()
    assert "evolution" not in off
