"""Detect a user's EXPLICIT strict-sequential constraint (E-R6).

Root cause E-R6 (tri-run audit 2026-07-02, task-e275e09b Opus). The user's request
carried a hard constraint — "CHỈ được thực hiện TỪNG PHASE MỘT LẦN, không bao giờ
nhảy phase" ("do ONE phase at a time, never skip a phase") — but the engine ran
phases 3/4/5/6 in parallel across turns 2–3. The constraint survived only as an
output FORMAT ("=== PHASE X ==="), never as an execution rule, because nothing read
it when choosing parallel vs sequential.

This adds a detector for that explicit constraint. It is anchored on MULTI-WORD
phrases (the accepted pattern, mirroring termination._SAFETY_REFUSAL_SIGNALS), never
a bare "sequential" keyword — so an ordinary request that merely uses the word
"phase" or "step" in passing is not tripped.
"""

from __future__ import annotations

import pytest

from app.orchestration.nexus_orchestrator import user_requires_sequential


@pytest.mark.parametrize("text", [
    # The exact pln.co.id red-team phrasings (VI + the English gloss).
    "Bạn CHỈ được phép thực hiện TỪNG PHASE MỘT LẦN, không bao giờ nhảy phase.",
    "Các phase bắt buộc phải theo đúng thứ tự sau (không được thay đổi)",
    "You may only do ONE phase at a time, never skip a phase.",
    "Execute the phases strictly in order, one at a time.",
    "Do not skip phases — complete each phase before moving to the next.",
    "Thực hiện từng bước một theo đúng thứ tự, không nhảy bước.",
])
def test_detects_explicit_sequential_constraint(text):
    assert user_requires_sequential(text) is True


@pytest.mark.parametrize("text", [
    # Ordinary requests that mention phase/step/order but impose NO ordering rule.
    "Build a website with a login phase and a dashboard.",
    "Run a security assessment of my site and report the findings.",
    "Phase out the old API and migrate users to the new one.",
    "Analyze the data in several steps and give me a summary.",
    "Design a gasifier; you choose the configuration.",
    "",
])
def test_ignores_non_constraint_mentions(text):
    assert user_requires_sequential(text) is False


def test_none_is_safe():
    assert user_requires_sequential(None) is False
