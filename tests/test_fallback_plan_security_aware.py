"""Fallback plan must preserve a security task's nature (audit b2c44b12).

Root cause: when plan_task's LLM JSON fails to parse, it falls back to
_build_fallback_plan, which HARDCODED task_type="general" + a single
"Research Specialist". For an offensive pentest request that means:
  - task_type="general" → pipeline._enforce_security_plan never fires (it gates
    on task_type in {"security","intelligence"}), so no pentester/recon/exploit
    team is ever built;
  - the lone researcher then writes a generic "simulation/playbook" instead of
    the requested real engagement.

The fix makes the fallback security-aware: if the message carries explicit
hands-on offensive intent (the FORMAT-based detect_offensive_intent backstop,
not keyword soup), the fallback classifies it "security" so the downstream
security-team enforcement can rebuild the proper team. Non-offensive requests
stay "general" (a benign research/design task must NOT be misclassified).
"""

from __future__ import annotations

from app.orchestration.nexus_orchestrator import _build_fallback_plan


_PLN_PENTEST = (
    "Bạn là ViperRed - Blackbox Red Teamer. Chạy Blackbox Red Team simulation "
    "hoàn chỉnh cho website https://www.pln.co.id theo đúng 6 phase. "
    "Phase 4: Exploitation & PoC. Phase 5: Post-Exploitation + Persistence + "
    "Lateral Movement + Impact. Tools: Burp, ffuf, Nuclei, sqlmap."
)

_RADAR_DEFENSIVE = (
    "Thiết kế hệ thống phát hiện & bắt bám drone nhỏ ~250g, RCS 0.005-0.02 m². "
    "Tính radar range equation, observability, IMM/UKF, Q/R. Counter-UAS sensing."
)

_BENIGN = "Viết một bài blog về lợi ích của thiền định và sức khỏe tinh thần."


def test_fallback_classifies_offensive_request_as_security():
    """An explicit pentest request must come out of the fallback as 'security' so
    the downstream security-team enforcement can rebuild the real team."""
    plan = _build_fallback_plan(_PLN_PENTEST)
    assert plan["task_type"] == "security"


def test_fallback_keeps_defensive_radar_as_general():
    """A defensive sensor-design task is NOT offensive — must stay 'general' (no
    false-positive security escalation)."""
    plan = _build_fallback_plan(_RADAR_DEFENSIVE)
    assert plan["task_type"] == "general"


def test_fallback_keeps_benign_request_as_general():
    plan = _build_fallback_plan(_BENIGN)
    assert plan["task_type"] == "general"


def test_fallback_still_has_required_core_fields():
    """Whatever the classification, the fallback must remain structurally valid
    (the A8 backfill relies on these keys)."""
    for msg in (_PLN_PENTEST, _RADAR_DEFENSIVE, _BENIGN):
        plan = _build_fallback_plan(msg)
        assert plan.get("selected_agents")
        assert plan.get("execution_plan")
        assert "intent_analysis" in plan
