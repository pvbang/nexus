"""Explicit, ordered registry of the orchestrator pipeline's phases.

``NexusPipeline.run`` (in ``pipeline.py``, ~2300 lines) executes a fixed
sequence of phases — context aggregation → planning → (divergent thinking) →
execution → final report → summarize — but that order is implicit in a single
very long method. This module mirrors the sequence as an explicit, testable
``MiddlewareChain`` so:

- the order is pinned by a test (regression guard for accidental reordering),
- feature-gated phases (divergent thinking, evolution) are declared once with
  their on/off flag and position,
- future refactors can move each phase's real logic into a named step without
  rewriting the whole method at once (the seam for decomposing the god-object).

This is the first decomposition slice: structure now, logic-move later.
"""

from __future__ import annotations

from app.orchestration.middleware_chain import MiddlewareChain, Before

# Canonical phase order when all default features are on. Kept in sync with the
# ═══ Phase N ═══ markers in pipeline.py.
PHASE_NAMES: tuple[str, ...] = (
    "evolution",            # Phase 0   — Evolution Agent (config-gated)
    "context_aggregation",  # Phase 0.5 — smart context aggregation
    "planning",             # Phase 1   — orchestrator planning
    "security_enforcement", # Phase 1.5 — security plan enforcement
    "divergent_thinking",   # Phase 1.6 — complex/expert tasks only
    "execution",            # Phase 3   — turn-based execution loop
    "final_report",         # Phase 4   — generate final report
    "summarize",            # Phase 5   — summarize for user
)


def build_pipeline_phases(
    *,
    evolution: bool = True,
    divergent_thinking: bool = True,
    security_enforcement: bool = True,
) -> MiddlewareChain:
    """Build the pipeline phase chain, honouring feature flags.

    Returns a MiddlewareChain whose ``resolved()`` is the ordered list of
    enabled phase names. Feature-gated phases drop out when their flag is False.
    """
    chain = MiddlewareChain()
    chain.add("evolution", enabled=evolution)
    chain.add("context_aggregation")
    chain.add("planning")
    chain.add("security_enforcement", enabled=security_enforcement)
    chain.add("execution")
    chain.add("final_report")
    chain.add("summarize")

    # Divergent thinking runs after planning but before execution.
    chain.add("divergent_thinking", enabled=divergent_thinking, position=Before("execution"))
    return chain
