"""H3 (audit 2026-06-29): the pipeline loop-detection hash must be stable across
the loop-breaker's own injected directive prefix.

Root cause: the assignment hash keyed on raw ``task_description[:200]``. When the
loop-breaker prepends "[LOOP DETECTED: ... COMPLETELY DIFFERENT approach]" to the
task, the next turn's text changes, the hash changes, and the occurrence counter
resets — so the warn(3)→terminate(5) ladder can never reach its ceiling. The
loop-breaker defeated its own counter.

Fix: hash on the NORMALIZED task text (which strips injected directive prefixes,
the same normalization similarity-dedup already uses) so a re-issued assignment
hashes identically whether or not it carries the loop/redo/evolution prefix.
"""

from __future__ import annotations

from app.orchestration.models import AgentAssignment
from app.orchestration.task_similarity import assignment_loop_hash


def _a(role: str, task: str) -> AgentAssignment:
    return AgentAssignment(
        agent_role=role,
        agent_name=role.title(),
        task_description=task,
        success_criteria="done",
    )


_BASE_TASK = "Scan pln.co.id for open ports and enumerate services on the host."
_LOOP_PREFIX = (
    "[LOOP DETECTED: This exact assignment was given 3+ times before with NO "
    "progress. You MUST use a COMPLETELY DIFFERENT approach. If your previous "
    "method failed, try: different tools, different target, different strategy, "
    "or report that this objective is BLOCKED.]\n\n"
)


def test_hash_ignores_loop_detected_prefix():
    """The same assignment with/without the loop-breaker prefix hashes identically."""
    plain = assignment_loop_hash([_a("recon", _BASE_TASK)])
    prefixed = assignment_loop_hash([_a("recon", _LOOP_PREFIX + _BASE_TASK)])
    assert plain == prefixed, (
        "the injected [LOOP DETECTED] prefix changed the hash — the occurrence "
        "counter would reset and the terminate ladder could never fire"
    )


def test_hash_ignores_evolution_prefix():
    """Other injected directives (Evolution/redo) are stripped too."""
    plain = assignment_loop_hash([_a("engineer", _BASE_TASK)])
    evo = assignment_loop_hash(
        [_a("engineer", "[Evolution: CHANGE APPROACH] " + _BASE_TASK)]
    )
    assert plain == evo


def test_hash_distinguishes_genuinely_different_work():
    """Different task content still produces different hashes (no over-collapse)."""
    a = assignment_loop_hash([_a("recon", _BASE_TASK)])
    b = assignment_loop_hash([_a("recon", "Write the final compliance report to disk.")])
    assert a != b


def test_hash_distinguishes_role():
    """Same task under a different role is a different assignment."""
    a = assignment_loop_hash([_a("recon", _BASE_TASK)])
    b = assignment_loop_hash([_a("pentester", _BASE_TASK)])
    assert a != b


def test_hash_is_order_independent():
    """A turn's assignment set hashes the same regardless of listing order."""
    one = assignment_loop_hash([_a("recon", "task A"), _a("writer", "task B")])
    two = assignment_loop_hash([_a("writer", "task B"), _a("recon", "task A")])
    assert one == two


def test_empty_normalized_task_does_not_collapse_to_role_only():
    """H3a: two assignments whose descriptions normalize to "" but differ in raw
    text must NOT hash identically (else empty-desc same-role assignments would
    falsely trip the loop terminator — asymmetry with semantic_loop_terminal)."""
    a = assignment_loop_hash([_a("recon", "...")])      # punctuation → "" normalized
    b = assignment_loop_hash([_a("recon", "???")])      # different raw, also "" normalized
    assert a != b, "distinct empty-normalize tasks collided to role-only hash"


def test_empty_normalize_fallback_still_strips_injected_prefix():
    """5-A (round-4): the empty-normalize fallback must NOT key on a turn-varying
    injected prefix — otherwise the SAME punctuation-only task re-issued with a
    loop-breaker prefix hashes differently each turn and the ladder never fires
    (re-introducing the very H3 bug for empty-normalize tasks)."""
    t3 = "[LOOP DETECTED: turn 3 directive here] !!!"
    t4 = "[LOOP DETECTED: turn 4 directive here] !!!"
    assert assignment_loop_hash([_a("recon", t3)]) == assignment_loop_hash([_a("recon", t4)]), (
        "empty-normalize fallback keyed on the injected prefix → hash drifts each turn"
    )
