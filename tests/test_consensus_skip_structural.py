"""Tests for P4: structural (not keyword) consensus-skip signal.

Audit §5 P4 — `_check_consensus_skip` previously sniffed thinking text for
disagreement keywords ("disagree", "concern", ...), a brittle heuristic (audit
M2). Discussion only earns its LLM cost when agents have CONFLICTING approaches
to the SAME target; when each agent owns a DISTINCT sub-task there is nothing to
cross-examine. The structural signal is assignment overlap (jaccard similarity
of task descriptions), reusing the already-tested `_task_similarity`.

Pure functions: no LLM, no IO.
"""

from __future__ import annotations

from app.orchestration.task_similarity import (
    _normalize_task_text,
    _task_similarity,
    assignments_overlap,
)
from app.orchestration.turn_executor import TurnExecutor  # noqa: F401


def _mk(role: str, desc: str):
    from app.orchestration.models import AgentAssignment

    return AgentAssignment(agent_role=role, agent_name=role.title(), task_description=desc)


# ─────────────── re-export parity (moved out of god-object) ───────────────


def test_helpers_reexported_from_orchestrator():
    """Moving the helpers to task_similarity.py must not break old imports."""
    from app.orchestration import nexus_orchestrator as no

    assert no._task_similarity is _task_similarity
    assert no._normalize_task_text is _normalize_task_text


# ───────────────────────── assignments_overlap ─────────────────────────


def test_overlap_true_when_two_agents_share_same_target():
    # Two agents pointed at essentially the same work → discussion has value.
    a = _mk("dev", "Build the REST API server with auth endpoints")
    b = _mk("backend", "Build the REST API server with auth endpoints")
    assert assignments_overlap([a, b]) is True


def test_no_overlap_when_agents_own_distinct_subtasks():
    # Disjoint sub-tasks → nothing to cross-examine → no overlap.
    a = _mk("frontend", "Build the React dashboard UI components")
    b = _mk("infra", "Provision the Kubernetes cluster and CI pipeline")
    assert assignments_overlap([a, b]) is False


def test_single_assignment_never_overlaps():
    assert assignments_overlap([_mk("solo", "do the thing")]) is False


def test_empty_never_overlaps():
    assert assignments_overlap([]) is False


def test_overlap_detected_in_any_pair_of_three():
    a = _mk("a", "Scrape the competitor pricing pages")
    b = _mk("b", "Render the final PDF report")
    c = _mk("c", "Scrape the competitor pricing pages")  # dup of a
    assert assignments_overlap([a, b, c]) is True


def test_threshold_is_tunable():
    a = _mk("a", "alpha beta gamma delta")
    b = _mk("b", "alpha beta gamma epsilon")  # jaccard 3/5 = 0.6
    assert assignments_overlap([a, b], threshold=0.5) is True
    assert assignments_overlap([a, b], threshold=0.8) is False


# ──────────── TurnExecutor._check_consensus_skip (structural) ────────────


def _mk_executor():
    """Minimal TurnExecutor for exercising the pure-ish consensus-skip method."""
    from app.orchestration.activity_logger import ActivityLogger
    from app.orchestration.context_sharing import ContextSharing
    from app.orchestration.turn_executor import TurnExecutor

    return TurnExecutor(
        workers={},
        context_sharing=ContextSharing(),
        activity_logger=ActivityLogger(task_id=""),
    )


def test_consensus_skip_when_assignments_distinct():
    # Distinct sub-tasks → nothing to cross-examine → skip discussion.
    ex = _mk_executor()
    assignments = [
        _mk("frontend", "Build the React dashboard UI components"),
        _mk("infra", "Provision the Kubernetes cluster and CI pipeline"),
    ]
    assert ex._check_consensus_skip(assignments) is True


def test_no_skip_when_assignments_overlap():
    # Two agents on the same target → keep discussion (real conflict possible).
    ex = _mk_executor()
    assignments = [
        _mk("dev", "Build the REST API server with auth endpoints"),
        _mk("backend", "Build the REST API server with auth endpoints"),
    ]
    assert ex._check_consensus_skip(assignments) is False


def test_consensus_skip_single_assignment():
    ex = _mk_executor()
    assert ex._check_consensus_skip([_mk("solo", "do the thing")]) is True


def test_consensus_skip_does_not_depend_on_disagreement_keywords():
    """Regression: the old heuristic skipped/kept based on words like 'concern'
    in the thinking text. The structural signal ignores wording entirely and
    decides purely from assignment overlap."""
    ex = _mk_executor()
    # Distinct sub-tasks whose descriptions happen to contain 'concern'/'issue'
    # must STILL skip — wording is irrelevant now.
    assignments = [
        _mk("a", "Address the security concern in the auth module"),
        _mk("b", "Fix the rendering issue in the PDF exporter"),
    ]
    assert ex._check_consensus_skip(assignments) is True


# ── synthesis/review role forces discussion (E-R9, tri-run audit 2026-07-02) ──


def test_no_skip_when_a_synthesis_role_is_present():
    """E-R9: on the Opus run a writer FABRICATED 'SQL Injection CVSS 9.8' on the
    exact turn discussion was skipped ('distinct sub-tasks'). A role that CONSUMES
    peers' work (writer/critic/security_reviewer/report/reviewer) has implicit
    overlap with everyone — cross-examination is most valuable there. Keep
    discussion whenever such a role is on the turn, even if task descriptions look
    disjoint."""
    ex = _mk_executor()
    assignments = [
        _mk("recon_specialist", "Enumerate subdomains and DNS records"),
        _mk("writer", "Compile the consolidated final report from all findings"),
    ]
    assert ex._check_consensus_skip(assignments) is False


def test_no_skip_when_critic_is_present():
    ex = _mk_executor()
    assignments = [
        _mk("engineer", "Size the throat and hearth from results.json"),
        _mk("critic", "Independently re-derive and try to refute the numbers"),
    ]
    assert ex._check_consensus_skip(assignments) is False


def test_still_skip_distinct_nonsynthesis_roles():
    """Regression: the synthesis-role rule must NOT make everything keep discussion.
    Distinct producer/analysis sub-tasks with no synthesis role still skip."""
    ex = _mk_executor()
    assignments = [
        _mk("frontend", "Build the React dashboard UI components"),
        _mk("infra", "Provision the Kubernetes cluster and CI pipeline"),
    ]
    assert ex._check_consensus_skip(assignments) is True


# ── honest skip label (task 3c44a93f bug #6c) ──────────────────────────────


def test_skip_synthesis_label_reflects_distinct_tasks_not_consensus():
    """When discussion is skipped because sub-tasks are DISTINCT, the recorded
    synthesis must say so — not 'Consensus detected / All agents aligned', which
    falsely implies agreement was reached (it wasn't; there was nothing to
    debate). Pin the honest wording."""
    text = TurnExecutor_skip_label()
    low = text.lower()
    assert "distinct" in low or "no overlap" in low
    assert "consensus detected" not in low
    assert "all agents are aligned" not in low


def TurnExecutor_skip_label() -> str:
    """Return the synthesis text TurnExecutor records when it skips discussion."""
    from app.orchestration.turn_executor import _skip_discussion_synthesis_text

    return _skip_discussion_synthesis_text()
