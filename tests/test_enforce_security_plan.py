"""Tests for _enforce_security_plan (audit C3 — security-critical, was untested).

When the planner classifies a task as security but under-plans (fewer than 3
security agents), this overrides with a proper 5-agent security team and a 4-turn
recon→vuln→exploit→report plan. Pins both the override and the no-op path.

Tested in-place (not extracted): it's ~30 lines of logic wrapped around ~100 lines
of static plan template — extracting it would add a module without reducing real
complexity. Coverage is the win here, not relocation.
"""

from __future__ import annotations

from app.orchestration.models import TurnPlan
from app.orchestration.pipeline import _enforce_security_plan


def _plan_with_agents(roles):
    return {
        "task_type": "security",
        "selected_agents": [{"role": r, "name": r.title()} for r in roles],
        "execution_plan": [],
    }


def _stub_first_plan():
    return TurnPlan(turn_number=1, objective="original")


# ───────────────────────── override path ─────────────────────────


def test_under_planned_security_gets_full_team():
    # Only 1 security agent → must be overridden with the 5-agent team.
    plan_data = _plan_with_agents(["pentester"])
    out_data, out_plan = _enforce_security_plan(plan_data, _stub_first_plan(), "test https://target.example.com")

    roles = [a["role"] for a in out_data["selected_agents"]]
    assert len(roles) == 5
    assert "recon_specialist" in roles
    assert "exploit_developer" in roles
    assert out_data["complexity"] == "expert"
    assert out_data["task_type"] == "security"


def test_override_builds_four_phase_plan():
    plan_data = _plan_with_agents([])
    out_data, out_plan = _enforce_security_plan(plan_data, _stub_first_plan(), "audit example.com")

    exec_plan = out_data["execution_plan"]
    assert len(exec_plan) == 4  # recon → vuln → exploit → report
    assert exec_plan[0]["turn"] == 1
    # First turn becomes the returned TurnPlan.
    assert out_plan.turn_number == 1
    assert out_plan.objective != "original"  # replaced with recon objective


def test_override_first_plan_has_recon_assignment():
    plan_data = _plan_with_agents(["pentester"])
    _, out_plan = _enforce_security_plan(plan_data, _stub_first_plan(), "scan target.io")
    roles = [a.agent_role for a in out_plan.assignments]
    assert "recon_specialist" in roles


# ───────────────────────── no-op path ─────────────────────────


def test_already_has_three_security_agents_is_noop():
    # 3+ security agents → plan returned unchanged (same objects).
    plan_data = _plan_with_agents(["pentester", "recon_specialist", "exploit_developer"])
    first = _stub_first_plan()
    out_data, out_plan = _enforce_security_plan(plan_data, first, "pentest example.com")
    assert out_plan is first  # unchanged
    assert out_data is plan_data
    assert len(out_data["selected_agents"]) == 3  # not overridden


def test_exactly_two_security_agents_triggers_override():
    # Boundary: 2 < 3 → override fires.
    plan_data = _plan_with_agents(["pentester", "recon_specialist"])
    out_data, _ = _enforce_security_plan(plan_data, _stub_first_plan(), "x")
    assert len(out_data["selected_agents"]) == 5  # overridden
