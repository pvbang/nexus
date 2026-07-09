"""Tests for measurable GOAL_ANCHOR success criteria (audit A7).

GOAL_ANCHOR's "SUCCESS WHEN" line read a non-existent top-level key and fell back to
echoing the prompt, so the orchestrator never had a measurable completion bar — the
root of "completion theater". The deliverable_contract the planner already produces
(definition_of_done + artifacts[].verify) is now the source of truth.
"""

from __future__ import annotations

from app.orchestration.nexus_orchestrator import (
    derive_success_criteria,
    render_goal_anchor,
)


# ───────────────────────── pure: derive_success_criteria ─────────────────────────


def test_uses_definition_of_done_and_artifact_verify():
    plan = {
        "intent_analysis": "build a thing",
        "deliverable_contract": {
            "definition_of_done": "the user can run the app and log in",
            "artifacts": [
                {"what": "Next.js app", "verify": "open URL on :19000 and complete a login"},
                {"what": "API", "verify": "POST /login returns 200 with a token"},
            ],
        },
    }
    out = derive_success_criteria(plan)
    assert "the user can run the app and log in" in out
    assert "open URL on :19000" in out          # artifact verify carried through
    assert "POST /login returns 200" in out
    # It must NOT just echo the bare intent when a contract exists.
    assert out != "build a thing"


def test_falls_back_to_intent_when_no_contract():
    plan = {"intent_analysis": "investigate the breach and report"}
    assert derive_success_criteria(plan) == "investigate the breach and report"


def test_empty_when_nothing_measurable():
    # No contract, no intent → empty (caller must be honest, NOT echo the message).
    assert derive_success_criteria({}) == ""
    assert derive_success_criteria({"intent_analysis": ""}) == ""


def test_artifact_what_used_when_verify_missing():
    plan = {
        "deliverable_contract": {
            "artifacts": [{"what": "a .pdf report"}],  # no 'verify'
        },
    }
    out = derive_success_criteria(plan)
    assert "a .pdf report" in out


def test_contract_without_artifacts_uses_definition_of_done():
    plan = {"deliverable_contract": {"definition_of_done": "all 9 sections delivered"}}
    assert "all 9 sections delivered" in derive_success_criteria(plan)


def test_never_echoes_raw_message():
    # A7 core: the criteria must not silently become the user's prompt text.
    plan = {"intent_analysis": ""}
    out = derive_success_criteria(plan)
    assert out == ""  # honest emptiness, not a pasted prompt


# ───────────────────────── persistence (resume keeps the bar) ─────────────────────────


def test_execution_state_carries_success_criteria():
    from app.orchestration.models import ExecutionState

    st = ExecutionState(task_id="t", success_criteria="Done when: app runs")
    assert st.success_criteria == "Done when: app runs"


def test_checkpoint_roundtrips_success_criteria():
    from app.core.checkpoint_persistence import CheckpointPersistence
    from app.orchestration.models import ExecutionState
    from app.orchestration.pipeline import NexusExecutionPipeline

    st = ExecutionState(task_id="t", user_message="m", success_criteria="Done when: 32/32 pass")
    blob = CheckpointPersistence._serialize_state(st)
    assert blob["success_criteria"] == "Done when: 32/32 pass"
    pipe = NexusExecutionPipeline(llm=None, tool_executor=None, task_id="t")
    restored = pipe._restore_or_create_state("m", {"state_json": blob, "turn_number": 2})
    assert restored.success_criteria == "Done when: 32/32 pass"


# ───────────────────────── GOAL_ANCHOR rendering (the user-visible fix) ─────────────────────────
# render_goal_anchor is the SHIPPED pure function pipeline.run() calls — testing it
# directly (not a hand-copied replica) means a change to the real rendering can't pass
# stale. No LLM needed.

def test_anchor_uses_contract_not_prompt_echo():
    msg = "Thiết kế máy phát điện khí hóa sinh khối 20 kWe production-ready"
    plan = {
        "intent_analysis": msg,
        "deliverable_contract": {
            "definition_of_done": "all 9 sections A-I delivered and validated",
            "artifacts": [{"what": "validation suite", "verify": "run it: 32/32 checks PASS"}],
        },
    }
    anchor = render_goal_anchor(msg, plan)
    assert "all 9 sections A-I delivered" in anchor
    assert "32/32 checks PASS" in anchor


def test_anchor_is_honest_when_no_criteria():
    anchor = render_goal_anchor("do something", {"intent_analysis": ""})
    assert "no measurable criteria derived" in anchor
    # Must NOT paste the prompt onto the SUCCESS WHEN line as a fake criterion.
    assert "SUCCESS WHEN: do something" not in anchor


def test_anchor_accepts_precomputed_criteria_for_resume():
    # On resume the criteria are restored from the checkpoint; render must accept
    # them directly without re-deriving from plan_data (which resume lacks).
    anchor = render_goal_anchor("do x", {}, success_criteria="Done when: tests pass")
    assert "SUCCESS WHEN: Done when: tests pass" in anchor


def test_goalprofile_reads_success_criteria_from_state_json_tier():
    # Regression: resume read success_criteria at the wrong checkpoint tier (top
    # level) so it was always "". It must read checkpoint["state_json"][...].
    from app.orchestration.goal_compliance import GoalProfile

    checkpoint = {"turn_number": 2, "state_json": {"success_criteria": "Done when: X"}}
    gp = GoalProfile.from_message(
        "msg",
        success_criteria=str(checkpoint.get("state_json", {}).get("success_criteria", "")),
    )
    assert gp.success_criteria == "Done when: X"


def test_long_user_request_keeps_far_more_than_300_chars():
    # A long, detailed request (6 deliverables + constraints) must not be cut to a
    # ~300-char stub in the anchor — the agent would lose the whole request
    # (Bug B, 2026-07-01). Keep the intent readable at a much larger budget.
    msg = (
        "Thiết kế hồ sơ manufacturing-ready cho hệ khí hóa trấu 20 kW. "
        "Deliverable 1: cơ sở thiết kế định lượng (mass/energy balance, syngas, LHV, ER, CGE). "
        "Deliverable 2: bản vẽ CAD 2D .dxf + 3D .step/.stl có kích thước. "
        "Deliverable 3: mô phỏng đã chạy + self-test. "
        "Deliverable 4: quy trình chế tạo + BOM nhà cung cấp VN. "
        "Deliverable 5: an toàn CO/áp/nhiệt + sơ đồ điện. "
        "Deliverable 6: dossier tổng hợp tiếng Việt. Ràng buộc: trấu VN, chế tạo được tại VN."
    )
    anchor = render_goal_anchor(msg, {"intent_analysis": msg})
    # the anchor's USER ASKED line must carry well beyond the old 300-char cap
    assert "Deliverable 5" in anchor or "sơ đồ điện" in anchor
    assert len(anchor) > 800


def test_overlong_request_points_to_task_brief():
    # When the request is longer than the anchor budget, the anchor must tell the
    # agent the full request lives in TASK_BRIEF (so nothing is silently lost).
    msg = "Xây hệ thống. " + ("Yêu cầu chi tiết số N. " * 400)  # very long
    anchor = render_goal_anchor(msg, {"intent_analysis": "build"})
    assert "TASK_BRIEF" in anchor


def test_short_request_has_no_task_brief_pointer_noise():
    # A short request fits entirely — no need for the "read TASK_BRIEF" pointer.
    anchor = render_goal_anchor("Dịch câu này sang tiếng Anh.", {"intent_analysis": "translate"})
    assert "TASK_BRIEF" not in anchor


def test_anchor_does_not_truncate_multi_artifact_verify():
    # A build with many artifacts must keep the LAST artifact's verify step — the
    # old hard [:400] cut dropped trailing verify steps (review IMPORTANT).
    arts = [
        {"what": f"service {i}", "verify": f"prove service {i} works end to end with a real request"}
        for i in range(6)
    ]
    plan = {"deliverable_contract": {"definition_of_done": "all services live", "artifacts": arts}}
    anchor = render_goal_anchor("build platform", plan)
    assert "prove service 5 works" in anchor  # last artifact's verify survived
