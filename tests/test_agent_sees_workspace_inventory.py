"""The executing agent must SEE what files already exist on disk (A-1 / R-1).

Context-arch audit (2026-07-03): the orchestrator (evaluator) is injected the full
workspace tree, but the AGENT that actually does the work is not — its
previous_context has goal_anchor + TODO + ledger key_data, but NO file inventory. So
the worker doesn't know which deliverables already exist → it re-does produced work
or skips a file it thinks is missing. This is the "trôi vô định, không biết đã có
gì" the user reported.

WorkspaceReader.build_work_inventory exposes the _work/ file listing (name + size,
empty-scaffold dirs flagged) so the agent-context builder can inject it.
"""

from __future__ import annotations

import importlib


def _mk_reader(tmp_path, monkeypatch, task_id="invtk01"):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.orchestration.workspace_reader as wr
    importlib.reload(wr)
    work = tmp_path / "tasks" / task_id / "_work"
    work.mkdir(parents=True)
    return wr.WorkspaceReader(task_id), work


def test_inventory_lists_existing_files(tmp_path, monkeypatch):
    r, work = _mk_reader(tmp_path, monkeypatch)
    (work / "design_basis.py").write_text("x=1", encoding="utf-8")
    (work / "cad").mkdir()
    (work / "cad" / "ga.dxf").write_text("0\nSECTION", encoding="utf-8")

    inv = r.build_work_inventory(max_chars=4000)
    assert "design_basis.py" in inv
    assert "ga.dxf" in inv


def test_inventory_flags_empty_when_nothing(tmp_path, monkeypatch):
    r, _ = _mk_reader(tmp_path, monkeypatch)
    inv = r.build_work_inventory(max_chars=4000)
    assert "empty" in inv.lower() or "EMPTY" in inv


def test_inventory_respects_char_budget(tmp_path, monkeypatch):
    r, work = _mk_reader(tmp_path, monkeypatch)
    for i in range(200):
        (work / f"file_{i:03}.md").write_text("data", encoding="utf-8")
    inv = r.build_work_inventory(max_chars=800)
    assert len(inv) <= 800
