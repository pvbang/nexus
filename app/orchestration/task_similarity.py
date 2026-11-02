"""
Nexus - Assignment similarity helpers.

Pure functions (no LLM, no I/O) for comparing agent task descriptions. Extracted
from the orchestrator god-object (audit C1) so they can be reused without pulling
in the whole orchestrator — notably by the turn executor's structural
consensus-skip signal (audit §5 P4), which replaces the old keyword heuristic.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.models import AgentAssignment


# Prefixes the orchestrator injects into task_description (loop/redo/failure
# warnings). They must be stripped before comparing assignments for similarity,
# otherwise two identical tasks look "different" only because one was annotated.
_INJECTED_PREFIX_MARKERS = (
    "[loop detected",
    "[do not redo",
    "[warning:",
    "[forecast pivot]",
    "[metacognitive",
    "[install required]",
    "[resolve contradiction",
    "[evolution",            # [Evolution], [Evolution: CHANGE APPROACH …], [Evolution Advisory]
    "[fix tautology",
    "[independent review",
    "[artifact mismatch",
    "[reconcile numbers",
    "[eval stalled",         # honest-stop directive
)


def _strip_injected_prefixes(text: str) -> str:
    """Remove leading injected directive blocks (loop/redo/evolution warnings).

    They can stack (separated by \\n\\n); each is a ``[...]`` bracket the
    orchestrator prepends and that varies turn-to-turn. Stripping them is what
    makes a re-issued assignment compare/hash the same whether or not it carries
    the annotation. Returns the remaining text (not lowercased/tokenized)."""
    t = (text or "").strip()
    changed = True
    while changed:
        changed = False
        low = t.lower()
        for marker in _INJECTED_PREFIX_MARKERS:
            if low.startswith(marker):
                # Cut to the end of the bracketed directive (first closing ']').
                close = t.find("]")
                if close != -1:
                    t = t[close + 1:].strip()
                    changed = True
                break
    return t


def _normalize_task_text(text: str) -> str:
    """Normalize a task description for similarity comparison.

    Strips injected directive prefixes, lowercases, and reduces to alphanumeric
    word tokens so punctuation/markdown differences don't defeat dedup.
    """
    t = _strip_injected_prefixes(text)
    return " ".join(re.findall(r"[a-z0-9#]+", t.lower()))


def assignment_loop_hash(assignments: list["AgentAssignment"]) -> str:
    """Stable 12-char hash of a turn's assignment SET for loop detection.

    H3 (audit 2026-06-29): the orchestrator counts how often the same assignment
    recurs to drive the warn(3)→terminate(5) ladder. Keying on raw task text let
    the loop-breaker's own injected "[LOOP DETECTED] … COMPLETELY DIFFERENT …"
    prefix change the hash every turn, resetting the counter so the ladder never
    reached its ceiling. Hashing on :func:`_normalize_task_text` (which strips the
    injected directive prefixes) makes a re-issued assignment hash identically
    whether or not it carries the loop/redo/evolution annotation.

    Order-independent: ``{role}:{normalized-task}`` signatures are sorted before
    hashing, so the same set of assignments hashes the same regardless of order.

    H3a guard: when a description normalizes to "" (pure punctuation, or a bare
    injected marker with nothing after), fall back to the RAW text for that part —
    otherwise distinct empty-normalize assignments to the same role would collapse
    to a role-only hash and falsely trip the loop terminator (the asymmetry the
    review flagged vs ``semantic_loop_terminal``'s empty-guard).
    """
    parts = []
    for a in assignments:
        norm = _normalize_task_text(a.task_description)
        # H3a + 5-A fix: when a description normalizes to "" (pure punctuation /
        # bare marker), fall back to the prefix-STRIPPED raw text — NOT the raw
        # text itself, which still carries the turn-varying injected prefix and
        # would make the hash drift every turn (re-introducing the H3 bug here).
        key = norm if norm else _strip_injected_prefixes(a.task_description)
        parts.append(f"{a.agent_role}:{key}")
    return hashlib.md5("|".join(sorted(parts)).encode()).hexdigest()[:12]


def _task_similarity(a: str, b: str) -> float:
    """Jaccard token similarity in [0,1] between two normalized task texts."""
    sa = set(_normalize_task_text(a).split())
    sb = set(_normalize_task_text(b).split())
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def assignments_overlap(
    assignments: list["AgentAssignment"],
    threshold: float = 0.6,
) -> bool:
    """Structural signal for whether multi-agent discussion is worthwhile.

    Discussion (challenge/rebuttal) only earns its per-agent LLM cost when two or
    more agents are working toward the SAME target — that is when their plans can
    actually conflict and cross-examination adds value. When every agent owns a
    DISTINCT sub-task there is nothing to debate.

    Returns True iff any pair of assignments has task-description similarity at or
    above `threshold`. Replaces the brittle keyword sniffing (audit §5 P4 / M2).
    """
    if len(assignments) <= 1:
        return False
    for i in range(len(assignments)):
        for j in range(i + 1, len(assignments)):
            if (
                _task_similarity(
                    assignments[i].task_description,
                    assignments[j].task_description,
                )
                >= threshold
            ):
                return True
    return False
