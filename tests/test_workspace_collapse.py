"""Audit c527e1c2 (RC3): agent code run with CWD=task _work/ wrote files to a
relative path that REPEATS the workspace prefix (e.g. savefig(
"workspace_data/tasks/<id>/_work/calc/x.png")), producing a nested duplicate
_work/workspace_data/tasks/<id>/_work/...  collapse_nested_workspace() is the
self-healing sweep that merges such a nested tree back into the real _work/."""
from __future__ import annotations

from app.mcp.tools.filesystem import collapse_nested_workspace


def _make(p):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("data", encoding="utf-8")


def test_collapses_nested_duplicate_into_work(tmp_path):
    ws = tmp_path / "_work"                      # the task's real _work/
    ws.mkdir()
    # The nested duplicate the bug creates: _work/workspace_data/tasks/<id>/_work/...
    nested = ws / "workspace_data" / "tasks" / "abcd1234" / "_work"
    _make(nested / "calc" / "range.png")
    _make(nested / "report" / "report.md")

    moved = collapse_nested_workspace(ws, ws_name="workspace_data")

    assert (ws / "calc" / "range.png").exists()      # merged up to real _work/
    assert (ws / "report" / "report.md").exists()
    assert not (ws / "workspace_data").exists()       # nested tree removed
    assert moved == 2


def test_does_not_overwrite_existing_shallow_file(tmp_path):
    # The shallow (real) file is authoritative; a nested duplicate must not clobber it.
    ws = tmp_path / "_work"
    ws.mkdir()
    _make(ws / "calc" / "range.png")
    (ws / "calc" / "range.png").write_text("REAL", encoding="utf-8")
    nested = ws / "workspace_data" / "tasks" / "abcd1234" / "_work"
    _make(nested / "calc" / "range.png")   # duplicate content "data"
    (nested / "calc" / "range.png").write_text("DUP", encoding="utf-8")

    collapse_nested_workspace(ws, ws_name="workspace_data")

    assert (ws / "calc" / "range.png").read_text(encoding="utf-8") == "REAL"
    assert not (ws / "workspace_data").exists()


def test_noop_when_no_nested_tree(tmp_path):
    ws = tmp_path / "_work"
    ws.mkdir()
    _make(ws / "report" / "r.md")
    moved = collapse_nested_workspace(ws, ws_name="workspace_data")
    assert moved == 0
    assert (ws / "report" / "r.md").exists()


def test_legit_workspace_data_dir_without_inner_work_left_untouched(tmp_path):
    # final-review check 5: a task that legitimately creates a dir named
    # "workspace_data" under _work/ (NO inner _work/ → not the bug's signature)
    # must NOT be flattened or deleted.
    ws = tmp_path / "_work"
    ws.mkdir()
    legit = ws / "workspace_data" / "tasks"
    _make(legit / "notes.txt")
    moved = collapse_nested_workspace(ws, ws_name="workspace_data")
    assert moved == 0
    assert (ws / "workspace_data" / "tasks" / "notes.txt").exists()  # untouched


def test_empty_ws_name_never_deletes_real_workspace(tmp_path):
    # final-review check 4: empty/dot/slash ws_name must NOT rmtree the real _work/.
    ws = tmp_path / "_work"
    ws.mkdir()
    _make(ws / "report" / "final.md")
    _make(ws / "calc" / "range.png")
    for bad in ("", ".", "/", "\\"):
        moved = collapse_nested_workspace(ws, ws_name=bad)
        assert moved == 0
    assert (ws / "report" / "final.md").exists()  # real workspace intact
    assert (ws / "calc" / "range.png").exists()
