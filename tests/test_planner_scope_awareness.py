"""Planner must SEE the declared pentest scope (architectural gap fix).

Audit fb1a7a3c: a target (www.pln.co.id) listed in pentest_scope.yaml was still
refused by the planner, because intent-analysis / plan_task never consulted the
scope — only the pre-flight gate (pipeline._check_authorization) and the runtime
tool gate (tool_executor) did. So the three layers disagreed: the planner would
refuse a target the gate would have authorized.

Fix: ``build_authorized_scope_note`` extracts the message's infra target, checks
it against the declared scope, and returns a short note (org + document) when the
target IS authorized — which plan_task injects into the planner's context (mirror
of the existing ``infra_context`` injection). An out-of-scope or non-infra message
yields an empty string, so nothing changes for the normal/blocked cases.
"""

from __future__ import annotations

import textwrap

import pytest

from app.security import pentest_scope as ps
from app.security.authorization_gate import build_authorized_scope_note


_SCOPE_YAML = textwrap.dedent("""
    - org: "Cong ty ABC"
      document: "SOW 45/2026 ngay 01/06/2026"
      targets: ["*.abc.com.vn", app.abc.com.vn]
""")


@pytest.fixture
def scope_file(tmp_path, monkeypatch):
    f = tmp_path / "pentest_scope.yaml"
    f.write_text(_SCOPE_YAML, encoding="utf-8")
    monkeypatch.setenv("PENTEST_SCOPE_FILE", str(f))
    ps.reload_scope()
    yield f
    monkeypatch.delenv("PENTEST_SCOPE_FILE", raising=False)
    ps.reload_scope()


def test_note_for_in_scope_target_names_org_and_document(scope_file):
    note = build_authorized_scope_note("pentest app.abc.com.vn full exploitation")
    assert note  # non-empty
    assert "app.abc.com.vn" in note
    assert "Cong ty ABC" in note
    assert "SOW 45/2026" in note
    assert "authorized" in note.lower()


def test_note_for_wildcard_subdomain_in_scope(scope_file):
    note = build_authorized_scope_note("recon api.abc.com.vn and exploit it")
    assert note
    assert "Cong ty ABC" in note


def test_no_note_for_out_of_scope_target(scope_file):
    # A real public target NOT in the declared scope → no authorization note, so
    # the planner is free to refuse exactly as before (no laundering).
    assert build_authorized_scope_note("pentest www.pln.co.id all 6 phases") == ""


def test_no_note_for_non_infra_message(scope_file):
    # No infra target at all → empty (never injects noise into benign planning).
    assert build_authorized_scope_note("design a biomass gasifier and write a report") == ""


def test_no_note_when_scope_empty(tmp_path, monkeypatch):
    # Fail-closed: with no scope file, nothing is authorized → empty note.
    monkeypatch.setenv("PENTEST_SCOPE_FILE", str(tmp_path / "absent.yaml"))
    ps.reload_scope()
    try:
        assert build_authorized_scope_note("pentest app.abc.com.vn") == ""
    finally:
        monkeypatch.delenv("PENTEST_SCOPE_FILE", raising=False)
        ps.reload_scope()


# ───────── plan_task actually injects the note into the planner context ─────────


@pytest.mark.asyncio
async def test_plan_task_injects_scope_note_for_in_scope_target(scope_file):
    """The note must reach the LLM planner's user message — proving the planner
    SEES the authorization (the architectural gap being closed)."""
    from app.orchestration.nexus_orchestrator import NexusOrchestrator

    captured = {}

    class _FakeLLM:
        async def chat(self, messages, **kwargs):
            captured["messages"] = messages
            return {"content": '{"task_type": "security", "complexity": "expert", '
                               '"selected_agents": [{"role": "pentester", "name": "P"}], '
                               '"execution_plan": [{"turn": 1, "objective": "recon", '
                               '"assignments": []}]}'}

    orch = NexusOrchestrator(llm=_FakeLLM())
    await orch.plan_task("Run a full pentest of app.abc.com.vn, all phases.")

    user_msg = captured["messages"][-1]["content"]
    assert "Authorized Engagement Scope" in user_msg
    assert "app.abc.com.vn" in user_msg
    assert "Cong ty ABC" in user_msg


@pytest.mark.asyncio
async def test_plan_task_no_note_for_out_of_scope_target(scope_file):
    """An out-of-scope target injects NO authorization note — the planner stays
    free to refuse (no laundering)."""
    from app.orchestration.nexus_orchestrator import NexusOrchestrator

    captured = {}

    class _FakeLLM:
        async def chat(self, messages, **kwargs):
            captured["messages"] = messages
            return {"content": '{"task_type": "security", "complexity": "expert", '
                               '"selected_agents": [{"role": "pentester", "name": "P"}], '
                               '"execution_plan": [{"turn": 1, "objective": "x", "assignments": []}]}'}

    orch = NexusOrchestrator(llm=_FakeLLM())
    await orch.plan_task("Run a full pentest of www.pln.co.id, all 6 phases.")

    user_msg = captured["messages"][-1]["content"]
    assert "Authorized Engagement Scope" not in user_msg
