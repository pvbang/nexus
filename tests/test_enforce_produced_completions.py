"""Producer 'completed' claims must be backed by production this turn (E5).

Run 68c276ac: engineer/developer/PM logged "BÁO CÁO HOÀN THÀNH NHIỆM VỤ: tạo
bản vẽ / file" with ZERO successful tool calls, and the orchestrator's
`agent_evaluations[].completed` (self-graded by the LLM) took the claim at face
value → the item entered "ALREADY COMPLETED (do NOT redo)" and fed false progress
credit. There was no code linking "a producer claims done" to "work reached disk".

`_enforce_produced_completions(agent_evaluations, produced_roles)` downgrades a
PRODUCER role's `completed=true` to False when that role produced no deliverable
this turn (its role absent from `produced_roles`). It only touches producer roles
(writer/engineer/developer/data_scientist/architect/...); analysis/verify roles
(critic/analyst/researcher/judge/reviewer/qa) legitimately finish a turn without
producing and are never downgraded. It does NOT change `success` — a prose turn
stays valid, it just can't claim a finished deliverable with nothing on disk.
"""

from __future__ import annotations

from app.orchestration.nexus_orchestrator import NexusOrchestrator as N


def test_producer_completed_without_production_is_downgraded():
    evals = [{"agent": "Eng", "role": "engineer", "completed": True, "notes": "made the drawing"}]
    out = N._enforce_produced_completions(evals, produced_roles=set())
    assert out[0]["completed"] is False
    assert "UNVERIFIED" in out[0]["notes"]


def test_producer_completed_with_production_is_kept():
    evals = [{"agent": "Eng", "role": "engineer", "completed": True, "notes": "wrote design.py"}]
    out = N._enforce_produced_completions(evals, produced_roles={"engineer"})
    assert out[0]["completed"] is True


def test_analysis_role_completed_without_production_is_kept():
    # A critic/analyst legitimately finishes a turn with no deliverable — never
    # downgrade these (phantom-write doctrine: don't kill analysis turns).
    evals = [
        {"agent": "Critic", "role": "critic", "completed": True, "notes": "refuted the LHV claim"},
        {"agent": "An", "role": "analyst", "completed": True, "notes": "sensitivity analysis done"},
        {"agent": "Re", "role": "researcher", "completed": True, "notes": "surveyed literature"},
    ]
    out = N._enforce_produced_completions(evals, produced_roles=set())
    assert all(ae["completed"] is True for ae in out)


def test_producer_not_completed_is_untouched():
    evals = [{"agent": "Dev", "role": "developer", "completed": False, "notes": "still coding"}]
    out = N._enforce_produced_completions(evals, produced_roles=set())
    assert out[0]["completed"] is False
    assert "UNVERIFIED" not in out[0]["notes"]


def test_mixed_turn_only_downgrades_the_unbacked_producer():
    evals = [
        {"agent": "W", "role": "writer", "completed": True, "notes": "wrote report"},   # no production
        {"agent": "D", "role": "developer", "completed": True, "notes": "ran sim"},      # produced
        {"agent": "C", "role": "critic", "completed": True, "notes": "verified"},        # analysis
    ]
    out = N._enforce_produced_completions(evals, produced_roles={"developer"})
    by_role = {ae["role"]: ae for ae in out}
    assert by_role["writer"]["completed"] is False
    assert by_role["developer"]["completed"] is True
    assert by_role["critic"]["completed"] is True


def test_empty_and_missing_role_safe():
    assert N._enforce_produced_completions([], set()) == []
    # An eval with no role key must not crash and is left as-is.
    evals = [{"agent": "X", "completed": True, "notes": "n"}]
    out = N._enforce_produced_completions(evals, set())
    assert out[0]["completed"] is True
