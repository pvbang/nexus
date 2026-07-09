"""P3 (CC-4): injected directive prefixes must be stripped before similarity.

Audit 2026-06-22: the orchestrator re-injects directive prefixes each turn
([RESOLVE CONTRADICTION], [Evolution: CHANGE APPROACH], …). The loop-dedup
normalizer only stripped a subset, so the SAME task carrying a new prefix looked
"different" and the loop guard never fired. These pin that all current injected
prefixes normalize away, so two identical tasks compare as identical regardless
of the directive bolted on front.
"""

from __future__ import annotations

import pytest

from app.orchestration.task_similarity import _normalize_task_text, _task_similarity


_BASE = "Scan the target host and enumerate open ports for the assessment."


# The bracketed directive (everything up to and including the first ']') is the
# annotation that must be stripped. Any free directive text the orchestrator adds
# AFTER the bracket is real content and is intentionally NOT stripped.
@pytest.mark.parametrize("prefix", [
    "[RESOLVE CONTRADICTION] ",
    "[Evolution: CHANGE APPROACH — try a different angle] ",
    "[METACOGNITIVE BREAKTHROUGH] ",
    "[FORECAST PIVOT] ",
    "[LOOP DETECTED] ",
    "[FIX TAUTOLOGY] ",
    "[INDEPENDENT REVIEW REQUIRED] ",
    "[ARTIFACT MISMATCH] ",
    "[RECONCILE NUMBERS] ",
])
def test_injected_prefix_stripped(prefix):
    # The base task with a bracketed directive must normalize to the same tokens
    # as the bare task → similarity 1.0 (loop guard sees them as the same work).
    assert _task_similarity(prefix + _BASE, _BASE) == pytest.approx(1.0)


def test_normalize_removes_prefix_text():
    norm = _normalize_task_text("[RESOLVE CONTRADICTION] do the thing")
    assert "resolve" not in norm and "contradiction" not in norm
    assert "do the thing".replace(" ", "") in norm.replace(" ", "")
