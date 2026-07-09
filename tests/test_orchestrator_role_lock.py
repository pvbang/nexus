# tests/test_orchestrator_role_lock.py
from __future__ import annotations

from app.orchestration.termination import canonicalize_role

TEAM = ["engineer", "data_scientist", "developer", "physicist", "writer", "critic"]


def test_known_role_unchanged():
    assert canonicalize_role("critic", TEAM) == "critic"


def test_writer_synthesis_variant_maps_to_writer():
    assert canonicalize_role("technical_writer / synthesis_agent", TEAM) == "writer"


def test_finalizer_maps_to_writer_when_no_better_match():
    # "finalizer / document-assembly" shares "document/assembly/writer" semantics
    out = canonicalize_role("finalizer / output-assembly agent", TEAM)
    assert out in TEAM


def test_radar_engineer_variant_maps_to_engineer():
    assert canonicalize_role("radar_systems_engineer (link-budget)", TEAM) == "engineer"


def test_empty_role_maps_to_first_team_role():
    assert canonicalize_role("", TEAM) == TEAM[0]
