"""Tests pinning the orchestrator's pure helper logic before refactor.

Covers the similarity/normalisation primitives behind assignment dedup, and the
keyword-matching edge cases of the terminal-state detector. Audit items M1/M2
flag this keyword-matching as brittle and slated for replacement by structured
signals — these tests capture the CURRENT behaviour (including its known
sharp edges) so a future rewrite can be compared against a known baseline.

All pure functions: no LLM, no IO.
"""

from __future__ import annotations

from app.orchestration.nexus_orchestrator import (
    _normalize_task_text,
    _task_similarity,
    dedupe_assignments,
    is_externally_blocked_terminal,
)


def _mk(role: str, desc: str):
    from app.orchestration.models import AgentAssignment

    return AgentAssignment(agent_role=role, agent_name=role.title(), task_description=desc)


# ───────────────────────── _normalize_task_text ─────────────────────────


def test_normalize_strips_injected_loop_prefix():
    base = "Poll telegram_get_updates once for an owner reply."
    injected = "[LOOP DETECTED: use a different approach]\n\n" + base
    assert _normalize_task_text(injected) == _normalize_task_text(base)


def test_normalize_strips_stacked_prefixes():
    base = "Fix the rotate-token parse offset."
    # Two real markers (see _INJECTED_PREFIX_MARKERS), each fully bracketed so the
    # cut-to-first-']' strip removes the whole block. The loop must peel both.
    stacked = "[LOOP DETECTED: x]\n\n[WARNING: y]\n\n" + base
    assert _normalize_task_text(stacked) == _normalize_task_text(base)


def test_normalize_reduces_to_alphanumeric_tokens():
    # Punctuation/markdown differences must not survive normalisation.
    assert _normalize_task_text("Fix **the** bug, now!") == _normalize_task_text("fix the bug now")


def test_normalize_handles_empty():
    assert _normalize_task_text("") == ""
    assert _normalize_task_text(None) == ""


# ───────────────────────── _task_similarity ─────────────────────────


def test_similarity_identical_is_one():
    assert _task_similarity("build the api server", "build the api server") == 1.0


def test_similarity_disjoint_is_zero():
    assert _task_similarity("alpha beta gamma", "delta epsilon zeta") == 0.0


def test_similarity_empty_is_zero():
    assert _task_similarity("", "anything here") == 0.0
    assert _task_similarity("anything here", "") == 0.0


def test_similarity_is_jaccard_symmetric():
    a, b = "fix the login bug", "fix the logout bug"
    # 3 shared tokens (fix,the,bug) / 5 unique (fix,the,login,logout,bug) = 0.6
    assert _task_similarity(a, b) == 0.6
    assert _task_similarity(a, b) == _task_similarity(b, a)


# ───────────────────────── dedupe threshold boundary ─────────────────────────


def test_dedupe_threshold_boundary_keeps_below_cut():
    # Two tasks at 0.6 similarity are below the 0.8 default → both kept.
    a = _mk("dev", "fix the login bug")
    b = _mk("qa", "fix the logout bug")
    kept, dropped = dedupe_assignments([a, b])
    assert len(kept) == 2 and dropped == []


def test_dedupe_custom_threshold_is_honoured():
    a = _mk("dev", "fix the login bug")
    b = _mk("qa", "fix the logout bug")  # similarity 0.6
    kept, dropped = dedupe_assignments([a, b], threshold=0.5)
    assert len(kept) == 1 and len(dropped) == 1
    assert kept[0].agent_role == "dev"  # first occurrence wins


def test_dedupe_empty_input():
    kept, dropped = dedupe_assignments([])
    assert kept == [] and dropped == []


# ───────── is_externally_blocked_terminal: keyword-matching edge cases (M2) ─────────


def test_terminal_actionable_verb_with_trailing_colon():
    # first_word strips a trailing ':' → "Fix:" still counts as actionable.
    assert is_externally_blocked_terminal(
        missing_items=["Owner reply pending (outside agent control)"],
        next_turn_focus="Fix: the failing build",
        stagnant=True,
    ) is False


def test_terminal_empty_missing_items_is_terminal_when_no_actionable_focus():
    # Stagnant, nothing concrete left, focus not actionable → terminal.
    assert is_externally_blocked_terminal(
        missing_items=[],
        next_turn_focus="Monitor-only: idle wait",
        stagnant=True,
    ) is True


def test_terminal_blank_focus_with_external_blockers():
    assert is_externally_blocked_terminal(
        missing_items=["Awaiting maintainer merge of PR #12"],
        next_turn_focus="",
        stagnant=True,
    ) is True


def test_terminal_requires_all_items_external():
    # One actionable item among external ones → NOT terminal.
    assert is_externally_blocked_terminal(
        missing_items=[
            "Owner reply pending (outside agent control)",
            "Refactor the parser module",  # no external signal → blocks terminal
        ],
        next_turn_focus="Waiting",
        stagnant=True,
    ) is False


def test_terminal_known_brittleness_kyc_substring():
    """DOCUMENTS a known sharp edge (audit M2): the bare 'kyc' substring matches.

    'KYC verification required' lowercases to contain 'kyc' so it IS treated as
    an external blocker. This is the brittle keyword behaviour we intend to
    replace with a structured signal; the test exists to make that replacement
    a deliberate, visible change rather than a silent regression.
    """
    assert is_externally_blocked_terminal(
        missing_items=["KYC verification required before payout"],
        next_turn_focus="Waiting",
        stagnant=True,
    ) is True
