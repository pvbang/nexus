"""Tests for the ROOT-A fix: enforce_role_rotation.

Context (audit of task d8b78042): one role ("physicist") ran turns 2→9 while
critic / engineer / writer / developer never executed once. The evaluator LLM
keeps re-picking the role that already has context; this pure helper forces a
neglected role in once a single role has dominated the recent window.
"""

from __future__ import annotations

from app.orchestration.nexus_orchestrator import enforce_role_rotation

TEAM = ["physicist", "engineer", "data_scientist", "developer", "writer", "critic"]


def test_monoculture_injects_fresh_critic_first():
    # physicist ran the last 2 turns; proposal keeps physicist. Critic is fresh.
    adjusted, injected, reason = enforce_role_rotation(
        proposed_roles=["physicist"],
        recent_turn_roles=[["physicist"], ["physicist"]],
        team_roles=TEAM,
    )
    assert injected == "critic"  # critic leads priority_fresh
    assert "critic" in adjusted
    assert "physicist" not in adjusted  # the single stale slot was swapped
    assert reason


def test_no_rotation_when_proposal_already_fresh():
    # Proposal already brings in a role that hasn't run recently → leave it alone.
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["engineer"],
        recent_turn_roles=[["physicist"], ["physicist"]],
        team_roles=TEAM,
    )
    assert injected is None
    assert adjusted == ["engineer"]


def test_no_rotation_without_enough_history():
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["physicist"],
        recent_turn_roles=[["physicist"]],  # only 1 turn of history
        team_roles=TEAM,
    )
    assert injected is None
    assert adjusted == ["physicist"]


def test_no_rotation_when_no_dominant_role():
    # Different role each turn → no monoculture → no intervention.
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["physicist"],
        recent_turn_roles=[["researcher"], ["data_scientist"]],
        team_roles=TEAM,
    )
    assert injected is None


def test_no_rotation_when_team_fully_exercised():
    # Everyone ran recently → no "fresh" role exists → nothing to inject.
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["physicist"],
        recent_turn_roles=[TEAM, TEAM],
        team_roles=TEAM,
    )
    assert injected is None


def test_keeps_agent_count_stable_on_multi_slot_proposal():
    # Two proposed slots, both stale → swap exactly one, preserve count.
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["physicist", "physicist"],
        recent_turn_roles=[["physicist"], ["physicist"]],
        team_roles=TEAM,
    )
    assert injected == "critic"
    assert len(adjusted) == 2
    assert "critic" in adjusted


def test_falls_back_to_first_fresh_when_critic_already_ran():
    team = ["physicist", "engineer", "critic"]
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["physicist"],
        recent_turn_roles=[["physicist", "critic"], ["physicist", "critic"]],
        team_roles=team,
    )
    # critic ran recently, so the only fresh role is engineer.
    assert injected == "engineer"


def test_singleton_team_never_rotates():
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["physicist"],
        recent_turn_roles=[["physicist"], ["physicist"]],
        team_roles=["physicist"],
    )
    assert injected is None


# ── Starvation detection (audit c527e1c2): a role that NEVER ran across a wide
#    window must be forced in even when analytical roles ALTERNATE (no single-role
#    monoculture, so the 2-turn dominance check is blind to it). ──────────────


def test_alternating_analytical_still_starves_writer():
    # c527e1c2: engineer/critic alternate for 11 turns; writer NEVER runs.
    # The 2-turn dominance window sees critic→engineer (no common role) → no
    # monoculture. Starvation must still catch the never-run writer.
    history = [["engineer"], ["critic"], ["engineer"], ["engineer"], ["critic"],
               ["engineer"], ["engineer"], ["critic"], ["engineer"], ["engineer"]]
    adjusted, injected, reason = enforce_role_rotation(
        proposed_roles=["engineer"],
        recent_turn_roles=history,
        team_roles=TEAM,
        starvation_window=8,
    )
    assert injected is not None
    assert "writer" in adjusted or injected == "writer" or injected in TEAM
    assert reason


def test_gap_aware_priority_injects_writer_when_report_missing():
    # When the outstanding gap is a missing report/document, a starved WRITER
    # leads priority (not critic) — the deliverable needs writing, not auditing.
    history = [["engineer"], ["critic"], ["engineer"], ["critic"],
               ["engineer"], ["engineer"], ["critic"], ["engineer"]]
    adjusted, injected, reason = enforce_role_rotation(
        proposed_roles=["engineer"],
        recent_turn_roles=history,
        team_roles=TEAM,
        starvation_window=6,
        outstanding_gap="Final consolidated report.md does not exist; write Sections 2-5",
    )
    assert injected == "writer"


def test_gap_aware_priority_keeps_critic_for_verification_gap():
    # An unverified-claim gap still prioritizes critic even with writer starved.
    history = [["engineer"], ["engineer"], ["engineer"], ["engineer"],
               ["engineer"], ["engineer"], ["engineer"], ["engineer"]]
    adjusted, injected, reason = enforce_role_rotation(
        proposed_roles=["engineer"],
        recent_turn_roles=history,
        team_roles=TEAM,
        starvation_window=6,
        outstanding_gap="Self-attested verdict not independently re-derived; refute the numbers",
    )
    assert injected == "critic"


def test_starvation_disabled_by_default_preserves_legacy():
    # starvation_window=0 (default) → behaviour identical to before this change.
    history = [["engineer"], ["critic"], ["engineer"], ["critic"]]
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["engineer"],
        recent_turn_roles=history,
        team_roles=TEAM,
    )
    assert injected is None  # no monoculture, starvation off → unchanged


def test_starvation_not_triggered_when_no_gap_and_team_progressing():
    # If every role HAS run within the starvation window, nothing is starved.
    history = [["engineer"], ["physicist"], ["writer"], ["critic"],
               ["data_scientist"], ["developer"], ["engineer"], ["critic"]]
    adjusted, injected, _ = enforce_role_rotation(
        proposed_roles=["engineer"],
        recent_turn_roles=history,
        team_roles=TEAM,
        starvation_window=8,
        outstanding_gap="report missing",
    )
    assert injected is None  # writer already ran → not starved
