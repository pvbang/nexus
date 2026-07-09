"""Path-resolver consistency across filesystem tools (task 3c44a93f bug #3).

read_file/write_file/delete_file/create_directory/list_files all resolve a
caller-supplied path through ``filesystem._smart_resolve_path`` (handles every
shape: relative, ``workspace_data/...`` prefix, ``tasks/...`` prefix, ``../``,
absolute). But search_files (``search_patch.py``) and edit_file
(``file_edit._resolve_file``) hand-rolled their own resolution
(``WORKSPACE_DIR / path`` / ``ws / path``) which does NOT strip the
``workspace_data/`` prefix. So when an agent passed the SAME full-prefix path
that read_file/write_file accept, those two tools returned a false
"Directory/File not found" — the mechanical cause of the 6-turn stall in
task 3c44a93f.

These tests pin: for every path shape that addresses a file in the task's
_work/, search_files and edit_file resolve to the SAME on-disk file that
write_file created.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fs_relative(tmp_path, monkeypatch):
    """Filesystem + search + edit tools with a RELATIVE WORKSPACE_DIR.

    Production default is ``WORKSPACE_DIR=workspace_data`` (relative). The buggy
    ``WORKSPACE_DIR / "workspace_data/tasks/..."`` double-prefix only misbehaves
    under a relative base, so we reproduce that exact runtime condition.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKSPACE_DIR", "workspace_data")
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.mcp.tools.filesystem as _fs
    importlib.reload(_fs)
    import app.mcp.tools.search_patch as _sp
    importlib.reload(_sp)
    import app.mcp.tools.file_edit as _fe
    importlib.reload(_fe)
    yield _fs, _sp, _fe
    tp._parent_cache.clear()


def _set_task_context(task_id: str):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    return current_task_id.set(task_id)


_TASK = "pathtk00"
_REL = "redteam/phase_1/recon.md"
_SHAPES = [
    _REL,                                              # plain relative
    "workspace_data/tasks/pathtk00/_work/" + _REL,     # full ws prefix (the bug)
    "tasks/pathtk00/_work/" + _REL,                    # missing ws_root prefix
]


@pytest.mark.asyncio
async def test_search_files_finds_subdir_file_for_all_path_shapes(fs_relative):
    """search_files must locate a file in a _work/ subdir regardless of the path
    shape the agent uses to address that subdir."""
    _fs, _sp, _fe = fs_relative
    from app.core.execution_context import current_task_id

    tok = _set_task_context(_TASK)
    try:
        await _fs.handle_write_file(
            {"path": _REL, "content": "UNIQUE_TOKEN_xyz in recon\n"}
        )
        for shape_dir in [
            "redteam/phase_1",
            "workspace_data/tasks/pathtk00/_work/redteam/phase_1",
            "tasks/pathtk00/_work/redteam/phase_1",
        ]:
            res = await _sp.handle_search_files(
                {"query": "UNIQUE_TOKEN_xyz", "path": shape_dir}
            )
            assert res["success"] is True, (
                f"search_files failed for shape {shape_dir!r}: {res['output']!r}"
            )
            assert "UNIQUE_TOKEN_xyz" in res["output"], (
                f"search_files found no match for shape {shape_dir!r}"
            )
    finally:
        current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_edit_file_resolves_subdir_file_for_all_path_shapes(fs_relative):
    """edit_file must edit a file in a _work/ subdir regardless of the path shape
    the agent uses (full ``workspace_data/...`` prefix included)."""
    _fs, _sp, _fe = fs_relative
    from app.core.execution_context import current_task_id

    for i, shape in enumerate(_SHAPES):
        tok = _set_task_context(_TASK)
        try:
            # Fresh file each iteration so each shape is an independent edit.
            await _fs.handle_write_file(
                {"path": _REL, "content": "old_value_marker\n"}
            )
            res = await _fe.handle_edit_file(
                {
                    "path": shape,
                    "edits": [
                        {"old_string": "old_value_marker", "new_string": f"new_value_{i}"}
                    ],
                }
            )
            assert res["success"] is True, (
                f"edit_file failed for shape {shape!r}: {res['output']!r}"
            )
            # Verify the edit actually landed on the canonical file on disk.
            ws = _fs.get_task_workspace()
            on_disk = (ws / _REL).read_text(encoding="utf-8")
            assert f"new_value_{i}" in on_disk, (
                f"edit for shape {shape!r} did not reach the canonical file"
            )
        finally:
            current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_search_files_still_rejects_traversal(fs_relative):
    """Negative: path traversal stays blocked after unifying the resolver."""
    _fs, _sp, _fe = fs_relative
    from app.core.execution_context import current_task_id

    tok = _set_task_context(_TASK)
    try:
        res = await _sp.handle_search_files(
            {"query": "root", "path": "../../../../etc"}
        )
        # Either a path-safety block or a clean "not found" — never a successful
        # search of an out-of-workspace directory.
        assert res["success"] is False or "etc/passwd" not in res["output"]
    finally:
        current_task_id.reset(tok)
