"""Empty directories are visible-but-not-deliverables (task 3c44a93f bug #1).

Turn 11 created phase_2..6/ via create_directory (all success). But the
orchestrator's workspace scan only counts FILES, so it saw "EMPTY — no
deliverables" and the metadata then asserted those dirs "remain on disk" without
ever checking. Two fixes:

1. _scan_work_inventory surfaces empty directories under a clearly-labelled
   "non-deliverable" section, so the orchestrator is no longer blind to them.
2. create_directory's success output states a bare directory is not a
   deliverable until it contains a file (so an empty dir is never mistaken for
   progress).
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def reader_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.orchestration.workspace_reader as wr
    importlib.reload(wr)
    yield wr, tp
    tp._parent_cache.clear()


def test_scan_surfaces_empty_dirs_as_non_deliverable(reader_env):
    wr, tp = reader_env
    task_id = "emptd0001"
    work = tp.work_dir(task_id)
    (work / "redteam" / "phase_1").mkdir(parents=True)
    (work / "redteam" / "phase_1" / "recon.md").write_text("real", encoding="utf-8")
    # Empty scaffold dirs — the 3c44a93f shape.
    for d in ("phase_2", "phase_3"):
        (work / "redteam" / d).mkdir(parents=True)

    reader = wr.WorkspaceReader(task_id)
    inventory, files = reader._scan_work_inventory()

    # Real file still counted.
    assert any("recon.md" in rel for rel, _, _ in files)
    # Empty dirs are surfaced AND labelled non-deliverable (not silently invisible).
    assert "phase_2" in inventory and "phase_3" in inventory
    assert "non-deliverable" in inventory.lower() or "empty" in inventory.lower()


def test_scan_no_empty_dirs_has_no_noise(reader_env):
    """When every dir holds a file, no empty-dir section is added."""
    wr, tp = reader_env
    task_id = "emptd0002"
    work = tp.work_dir(task_id)
    (work / "a").mkdir(parents=True)
    (work / "a" / "f.md").write_text("x", encoding="utf-8")

    reader = wr.WorkspaceReader(task_id)
    inventory, _ = reader._scan_work_inventory()

    assert "non-deliverable" not in inventory.lower()


@pytest.mark.asyncio
async def test_create_directory_warns_not_a_deliverable(reader_env, monkeypatch):
    wr, tp = reader_env
    import app.mcp.tools.filesystem as fs
    importlib.reload(fs)
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    tok = current_task_id.set("emptd0003")
    try:
        res = await fs.handle_create_directory({"path": "phase_2"})
        assert res["success"] is True
        # Output must make clear an empty dir is not itself a deliverable.
        assert "deliverable" in res["output"].lower()
    finally:
        current_task_id.reset(tok)
