"""A leading `_work/` in a write path must not create a nested `_work/_work/` tree.

Root cause S2 (cross-session audit 2026-06-30, Opus 4.8 run): an agent passed
``write_file(path="_work/redteam_pln/EXECUTIVE_REPORT.md")``. The tool's workspace
is ALREADY the task's ``_work/``; ``_smart_resolve_path`` only stripped a leading
``workspace_data/`` (Case 2) and the embedded-``_work`` marker case required a
segment BEFORE the marker (idx >= 1), so a LEADING ``_work/`` fell through to the
"resolve against task workspace" branch → ``_work / "_work/redteam_pln/..."`` =
``_work/_work/...``. The headline EXECUTIVE_REPORT.md landed ONLY in that
duplicate tree, orphaned from the canonical deliverable tree.

``collapse_nested_workspace`` did not heal it either: it only matched the RC3
signature ``_work/workspace_data/.../_work`` (it searches for an inner ``_work``
under a ``workspace_data`` dir), never ``_work/_work``.

These tests pin both: the resolver must collapse a redundant leading ``_work/``,
and the self-heal must merge a ``_work/_work`` duplicate back up.
"""

from __future__ import annotations

from pathlib import Path

from app.mcp.tools.filesystem import _smart_resolve_path, collapse_nested_workspace


def _ws(tmp_path):
    """A task work dir named `_work` plus the workspace root, as production passes."""
    ws_root = tmp_path / "workspace_data"
    ws = ws_root / "tasks" / "abcd1234" / "_work"
    ws.mkdir(parents=True)
    return ws, ws_root


# ── resolver: a leading `_work/` is a redundant prefix, not a child dir ──

def test_leading_work_prefix_does_not_nest(tmp_path):
    ws, ws_root = _ws(tmp_path)
    resolved = _smart_resolve_path("_work/redteam_pln/EXECUTIVE_REPORT.md", ws, ws_root)
    assert resolved == ws / "redteam_pln" / "EXECUTIVE_REPORT.md"
    # Crucially, NOT _work/_work/...
    assert "_work/_work" not in resolved.as_posix()


def test_doubled_leading_work_prefix_collapses(tmp_path):
    ws, ws_root = _ws(tmp_path)
    resolved = _smart_resolve_path("_work/_work/x.md", ws, ws_root)
    assert resolved == ws / "x.md"
    assert "_work/_work" not in resolved.as_posix()


def test_bare_work_dir_location_resolves_to_ws(tmp_path):
    ws, ws_root = _ws(tmp_path)
    resolved = _smart_resolve_path("_work/", ws, ws_root)
    assert resolved == ws


# ── regression: forms the resolver already handled must be unchanged ──

def test_plain_relative_path_unchanged(tmp_path):
    ws, ws_root = _ws(tmp_path)
    assert _smart_resolve_path("redteam_pln/recon.md", ws, ws_root) == ws / "redteam_pln" / "recon.md"


def test_workspace_data_prefix_still_stripped(tmp_path):
    ws, ws_root = _ws(tmp_path)
    # Full path that embeds _work later — Case 5 marker logic still applies.
    resolved = _smart_resolve_path(
        "workspace_data/tasks/abcd1234/_work/recon/x.txt", ws, ws_root
    )
    assert resolved == ws / "recon" / "x.txt"


def test_a_subdir_literally_named_workish_is_not_stripped(tmp_path):
    # Only the EXACT segment "_work" is a prefix repeat; "_workspace" is a real dir.
    ws, ws_root = _ws(tmp_path)
    resolved = _smart_resolve_path("_workspace/notes.md", ws, ws_root)
    assert resolved == ws / "_workspace" / "notes.md"


# ── self-heal: a `_work/_work` duplicate must be merged back up ──

def test_collapse_heals_nested_work_work(tmp_path):
    ws = tmp_path / "_work"
    ws.mkdir()
    nested = ws / "_work" / "redteam_pln"
    nested.mkdir(parents=True)
    (nested / "EXECUTIVE_REPORT.md").write_text("the headline report", encoding="utf-8")

    moved = collapse_nested_workspace(ws, ws_name="workspace_data")

    assert (ws / "redteam_pln" / "EXECUTIVE_REPORT.md").exists()
    assert not (ws / "_work").exists()
    assert moved == 1


def test_collapse_work_work_does_not_clobber_shallow(tmp_path):
    ws = tmp_path / "_work"
    ws.mkdir()
    (ws / "r.md").write_text("REAL", encoding="utf-8")
    nested = ws / "_work"
    nested.mkdir()
    (nested / "r.md").write_text("DUP", encoding="utf-8")

    collapse_nested_workspace(ws, ws_name="workspace_data")

    assert (ws / "r.md").read_text(encoding="utf-8") == "REAL"
    assert not (ws / "_work").exists()
