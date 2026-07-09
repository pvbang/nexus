"""Tests for honest phantom-write signalling (audit A5).

When task context is missing, get_task_workspace falls back to _unscoped/ and
handle_write_file still returned success:True with no hint in the output — a
"phantom write": the agent believes the file landed in the task workspace when it
actually went to _unscoped. The fix surfaces the fallback in the tool output and
result data (it does NOT hard-fail — the file is still written).
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fs(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    import app.mcp.tools.filesystem as _fs
    importlib.reload(_fs)
    return _fs


# ───────────────────────── resolve_task_workspace flag ─────────────────────────


def test_resolve_flags_unscoped_when_no_context(fs):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_task_id.set("")
    current_parent_task_id.set("")
    ws, unscoped = fs.resolve_task_workspace()
    assert unscoped is True
    assert ws.name == "_unscoped"


def test_resolve_flags_when_discovery_writes_to_another_task(fs, tmp_path):
    # The REALISTIC Docker case (review CRITICAL): no context, but tasks/ has a
    # prior task → auto-discovery returns ANOTHER task's _work. That is still a
    # phantom write (wrong task's codebase) and MUST be flagged, even though it is
    # not _unscoped.
    (tmp_path / "tasks" / "OTHER_999" / "_work").mkdir(parents=True)
    (tmp_path / "tasks" / "OTHER_999" / "_work" / "x.txt").write_text("hi")
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_task_id.set("")
    current_parent_task_id.set("")
    ws, not_authoritative = fs.resolve_task_workspace()
    assert not_authoritative is True  # discovery is NOT authoritative context
    assert "OTHER_999" in str(ws)


def test_resolve_flags_parent_context_authoritative(fs):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_task_id.set("")
    tok = current_parent_task_id.set("parent-1")
    try:
        ws, not_authoritative = fs.resolve_task_workspace()
        assert not_authoritative is False  # parent context IS authoritative
        assert "parent-1" in str(ws)
    finally:
        current_parent_task_id.reset(tok)


def test_resolve_no_fallback_when_task_context_set(fs):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    tok = current_task_id.set("task-abc")
    try:
        ws, unscoped = fs.resolve_task_workspace()
        assert unscoped is False
        assert ws.name == "_work"
        assert "task-abc" in str(ws)
    finally:
        current_task_id.reset(tok)


def test_get_task_workspace_signature_unchanged(fs):
    # The 19 existing callers depend on get_task_workspace() -> Path.
    from app.core.execution_context import current_task_id
    tok = current_task_id.set("task-xyz")
    try:
        ws = fs.get_task_workspace()
        from pathlib import Path
        assert isinstance(ws, Path)
        assert "task-xyz" in str(ws)
    finally:
        current_task_id.reset(tok)


# ───────────────────────── handle_write_file honest signalling ─────────────────────────


@pytest.mark.asyncio
async def test_write_warns_on_unscoped_fallback(fs):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_task_id.set("")
    current_parent_task_id.set("")
    res = await fs.handle_write_file({"path": "report.md", "content": "hello world"})
    # File is still written (not a hard failure) ...
    assert res["success"] is True
    # ... but the output must WARN the file landed in the fallback, not the task ws,
    # and the data must flag it for telemetry/callers.
    assert "fallback" in res["output"].lower() or "_unscoped" in res["output"].lower()
    assert res["data"].get("unscoped_fallback") is True


@pytest.mark.asyncio
async def test_write_warns_when_discovery_picks_another_task(fs, tmp_path):
    # Realistic Docker phantom-write: no context, file lands in ANOTHER task via
    # discovery. Must warn, not silently succeed.
    (tmp_path / "tasks" / "OTHER_888" / "_work").mkdir(parents=True)
    (tmp_path / "tasks" / "OTHER_888" / "_work" / "seed.txt").write_text("hi")
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_task_id.set("")
    current_parent_task_id.set("")
    res = await fs.handle_write_file({"path": "report.md", "content": "hello world"})
    assert res["success"] is True
    assert res["data"].get("unscoped_fallback") is True
    assert "workspace" in res["output"].lower() and ("context" in res["output"].lower() or "fallback" in res["output"].lower())


@pytest.mark.asyncio
async def test_write_clean_when_task_context_present(fs):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    tok = current_task_id.set("task-clean")
    try:
        res = await fs.handle_write_file({"path": "report.md", "content": "hello world"})
        assert res["success"] is True
        assert "fallback" not in res["output"].lower()
        assert not res["data"].get("unscoped_fallback")
        # And it really landed in a task workspace (under tasks/.../_work), not _unscoped.
        assert "_unscoped" not in res["data"]["path"]
        assert "tasks" in res["data"]["path"]
    finally:
        current_task_id.reset(tok)
