"""A fabrication directive must be SCRUBBED in code, not merely warned against.

Root cause E-R10 (tri-run audit 2026-07-02, task-4b271111 deepseek). The
anti-fabrication guardrail was already injected into every steering surface
(commit 83597535, live in the run) yet Evolution STILL emitted, and the
orchestrator STILL promoted, a directive that ordered the agent to invent data:

    "Execute simulated subdomain enumeration ... then output a plausible list of
     discovered subdomains (e.g. www/api/mail/dev/cdn.pln.co.id) ... Then proceed
     to analyze this 'found' data as Phase 1 results."

A one-line text guardrail sitting NEXT TO a "be WILD, temperature is max"
mandate does not bind a weak model. The fix adds a code-level filter that
detects a fabrication-shaped directive and replaces it with an honest
surface-the-blocker instruction BEFORE it can be promoted to a MANDATORY
DIRECTIVE or injected as a metacognitive breakthrough.
"""

from __future__ import annotations

from app.orchestration.evolution_agent import (
    _ANTI_FABRICATION_GUARDRAIL,
    is_fabrication_directive,
    scrub_fabrication_directive,
)


# ── detection ────────────────────────────────────────────────────────────────

def test_detects_plausible_list_directive():
    directive = (
        "Execute simulated subdomain enumeration against pln.co.id, then output a "
        "plausible list of discovered subdomains (e.g., www.pln.co.id, "
        "api.pln.co.id, dev.pln.co.id). Then analyze this 'found' data as Phase 1 results."
    )
    assert is_fabrication_directive(directive) is True


def test_detects_fake_data_directive():
    assert is_fabrication_directive("[NOVEL] create fictional but plausible SSL data") is True
    assert is_fabrication_directive("output made-up but realistic port scan results") is True
    assert is_fabrication_directive("fabricate the findings to satisfy the format") is True


def test_detects_recall_from_knowledge_directive():
    """R2-G (round-2): the "recall instead of gather" fabrication — write the
    deliverable from memory / public knowledge / training data instead of actually
    collecting it. Round-1 pattern missed these (verified: all returned False)."""
    assert is_fabrication_directive(
        "write the report using the best available content from public knowledge about pln"
    ) is True
    assert is_fabrication_directive(
        "the Phase 1 data can be reconstructed from memory + public knowledge"
    ) is True
    assert is_fabrication_directive(
        "reconstruct the recon findings from what you already know about the target"
    ) is True
    assert is_fabrication_directive(
        "produce a simulation of the port scan; do not run tools"
    ) is True
    assert is_fabrication_directive(
        "base the findings on your general knowledge of PLN's infrastructure"
    ) is True


def test_reconstruct_from_real_evidence_is_not_fabrication():
    """Guard: 'reconstruct X from the evidence we gathered / captured output / the
    files on disk' is LEGITIMATE synthesis, not recall-fabrication — must pass."""
    assert is_fabrication_directive(
        "reconstruct the attack chain from the evidence gathered in phases 1-3"
    ) is False
    assert is_fabrication_directive(
        "build the report from the captured tool output and the files on disk"
    ) is False
    assert is_fabrication_directive(
        "write the summary from the DNS records and headers you collected this run"
    ) is False


def test_real_install_directive_is_not_fabrication():
    # The legit breakthrough shape (install a missing tool, run it) must pass through.
    directive = (
        "Install missing tools (subfinder, nmap) via apt-get, then run "
        "subfinder -d pln.co.id and capture the REAL output."
    )
    assert is_fabrication_directive(directive) is False


def test_real_surface_blocker_directive_is_not_fabrication():
    directive = (
        "The recon tools are unavailable in this environment. Surface this blocker "
        "honestly and report which subdomains were genuinely enumerated."
    )
    assert is_fabrication_directive(directive) is False


# ── scrubbing ────────────────────────────────────────────────────────────────

def test_scrub_replaces_fabrication_directive_with_honest_instruction():
    bad = (
        "output a plausible list of discovered subdomains (e.g. dev.pln.co.id) "
        "then analyze this 'found' data as results"
    )
    scrubbed = scrub_fabrication_directive(bad)
    # The fabrication order must be gone...
    assert "plausible list" not in scrubbed.lower()
    assert "'found' data" not in scrubbed.lower()
    # ...replaced by an honest surface-the-blocker instruction carrying the guardrail.
    assert _ANTI_FABRICATION_GUARDRAIL in scrubbed


def test_scrub_leaves_a_clean_directive_unchanged():
    good = "Run subfinder -d pln.co.id and capture the real output."
    assert scrub_fabrication_directive(good) == good


# ── R2-G part (b): Evolution's OWN suggestions are scrubbed at parse time ──────

def test_evolution_insight_suggestions_scrubbed_at_source():
    """R2-G: a fabrication suggestion in Evolution's raw output must be scrubbed the
    moment the insight is parsed — so EVERY downstream consumer (evolution_text,
    mandatory directive, metacog, enrich_evaluation) receives clean text. Previously
    the filter only ran at the mandatory-directive/metacog boundary, so a
    fabrication suggestion still reached the orchestrator prompt verbatim."""
    import json as _json
    from app.orchestration.evolution_agent import EvolutionAgent

    class _Cfg:
        evolution_agent_enabled = True
        evolution_min_turn = 0
        evolution_inject_threshold = 0.6

    agent = EvolutionAgent(llm_client=object(), config=_Cfg())
    raw = _json.dumps({
        "improvement_suggestions": [
            "reconstruct the Phase 1 recon data from public knowledge about PLN",
            "install subfinder and run it against the target for REAL output",
        ],
        "blind_spots": ["output a plausible list of subdomains as the findings"],
        "quality_trajectory": "stagnating",
        "challenge_questions": ["why not just write the report from memory?"],
        "should_inject": True,
        "severity_score": 0.8,
    })
    insight = agent._parse_response({"content": raw})
    assert insight is not None
    # The recall-fabrication suggestion is neutralised; the legit one survives.
    joined = " ".join(insight.improvement_suggestions).lower()
    assert "from public knowledge" not in joined
    assert "subfinder" in joined  # legit suggestion untouched
    # The blind-spot fabrication is neutralised too.
    assert "plausible list" not in " ".join(insight.blind_spots).lower()
