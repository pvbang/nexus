"""`workspace_data/<deliverable>/` must resolve INTO the task _work/, not flatten.

Root cause E-R12/E-R3 (tri-run audit 2026-07-02, task-4b271111 deepseek). The
planner's CONTRACT declared artifact locations like ``workspace_data/recon/`` and
``workspace_data/exploits/`` (a top-level deliverable group, NOT the absolute
``workspace_data/tasks/<id>/_work/...`` path). When an agent wrote
``workspace_data/recon/cert.json`` following that contract:

  - _smart_resolve_path Case 2 stripped ``workspace_data/`` and joined the rest to
    the GLOBAL workspace root → ``<ws_root>/recon/cert.json`` — OUTSIDE the task
    _work/ → the write-handler then flattened it to ``_work/cert.json`` (subdir
    ``recon/`` lost) and warned "PATH RELOCATED".
  - the SAME logical dir addressed as a plain relative ``recon/whois.txt`` resolved
    correctly to ``_work/recon/whois.txt``.

So two path shapes for the same intent landed in two places → the agent wrote with
one, read back with the other, got "not found", and looped (reproduced live:
recon/ was perpetually "empty").

Fix: in Case 2, only treat the stripped remainder as an absolute-from-root path
when it addresses the tasks tree (``tasks/...``). Otherwise it is a deliverable
group of the CURRENT task → resolve it INTO the task _work/.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fs_relative(tmp_path, monkeypatch):
    """Filesystem tools under the production-default RELATIVE WORKSPACE_DIR."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKSPACE_DIR", "workspace_data")
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.mcp.tools.filesystem as _fs
    importlib.reload(_fs)
    yield _fs
    tp._parent_cache.clear()


def _set_task_context(task_id: str):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    return current_task_id.set(task_id)


# ── unit: _smart_resolve_path ────────────────────────────────────────────────

def test_contract_group_dir_resolves_into_task_work(tmp_path):
    """workspace_data/recon/x.json (a CONTRACT deliverable group) → task _work/recon/x.json."""
    from app.mcp.tools.filesystem import _smart_resolve_path

    ws_root = tmp_path / "workspace_data"
    ws = ws_root / "tasks" / "t1" / "_work"
    ws.mkdir(parents=True)

    resolved = _smart_resolve_path("workspace_data/recon/cert.json", ws, ws_root)
    assert resolved == ws / "recon" / "cert.json", (
        f"expected under task _work/, got {resolved}"
    )


def test_absolute_tasks_path_still_resolves_to_that_task(tmp_path):
    """workspace_data/tasks/<id>/_work/... is an ABSOLUTE addressed path → unchanged."""
    from app.mcp.tools.filesystem import _smart_resolve_path

    ws_root = tmp_path / "workspace_data"
    ws = ws_root / "tasks" / "t1" / "_work"
    ws.mkdir(parents=True)

    p = "workspace_data/tasks/t1/_work/redteam/recon.md"
    resolved = _smart_resolve_path(p, ws, ws_root)
    assert resolved == ws_root / "tasks" / "t1" / "_work" / "redteam" / "recon.md"


def test_bare_workspace_file_resolves_into_task_work(tmp_path):
    """workspace_data/report.md (no subdir) → task _work/report.md, not ws_root/report.md."""
    from app.mcp.tools.filesystem import _smart_resolve_path

    ws_root = tmp_path / "workspace_data"
    ws = ws_root / "tasks" / "t1" / "_work"
    ws.mkdir(parents=True)

    resolved = _smart_resolve_path("workspace_data/report.md", ws, ws_root)
    assert resolved == ws / "report.md"


# ── e2e: the exact DeepSeek scenario — write then read the SAME contract path ──

@pytest.mark.asyncio
async def test_contract_path_write_then_read_is_consistent(fs_relative):
    """Writing to a CONTRACT ``workspace_data/recon/`` path and reading it back with
    the SAME path must round-trip (no relocation, no 'not found' loop)."""
    _fs = fs_relative
    from app.core.execution_context import current_task_id

    tok = _set_task_context("deepbug1")
    try:
        w = await _fs.handle_write_file(
            {"path": "workspace_data/recon/cert.json", "content": "CERT_DATA"}
        )
        assert w["success"] is True
        # No relocation warning — it must land where asked.
        assert "RELOCATED" not in w["output"], (
            "contract path was flattened/relocated — the DeepSeek bug"
        )
        # Reading back with the SAME contract path must succeed.
        r = await _fs.handle_read_file({"path": "workspace_data/recon/cert.json"})
        assert r["success"] is True, "could not read back the file at its contract path"
        assert "CERT_DATA" in r["output"]

        # And the file physically lives under the task _work/recon/ (subdir kept).
        ws = _fs.get_task_workspace()
        assert (ws / "recon" / "cert.json").is_file()
    finally:
        current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_two_path_shapes_same_dir_land_together(fs_relative):
    """The two shapes for the same recon/ dir must land in the SAME directory."""
    _fs = fs_relative
    from app.core.execution_context import current_task_id

    tok = _set_task_context("deepbug2")
    try:
        await _fs.handle_write_file(
            {"path": "workspace_data/recon/a.txt", "content": "A"}
        )
        await _fs.handle_write_file(
            {"path": "recon/b.txt", "content": "B"}
        )
        ws = _fs.get_task_workspace()
        assert (ws / "recon" / "a.txt").is_file(), "workspace_data/recon/ shape flattened"
        assert (ws / "recon" / "b.txt").is_file(), "relative recon/ shape misplaced"
    finally:
        current_task_id.reset(tok)
