"""Regression test: write_file must PRESERVE subdirectories.

Audit finding (task 1f6decce): handle_write_file silently flattened any write
into a subdirectory back to _work/ root. Root cause at filesystem.py:589 —
``full_path.relative_to(WORKSPACE_DIR.resolve())`` compared a RELATIVE full_path
against an ABSOLUTE base, so it ALWAYS raised ValueError and the except branch
rewrote the path to ``ws / basename`` (subdir stripped).

Why it stayed invisible: every existing fixture (conftest.py:23, the A5 tests…)
sets ``WORKSPACE_DIR = str(tmp_path)`` — an ABSOLUTE path — under which the buggy
branch never fires. Production default is ``WORKSPACE_DIR=workspace_data`` — a
RELATIVE path (.env.example) — which triggers the flatten. These tests therefore
pin the RELATIVE-WORKSPACE_DIR case explicitly.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fs_relative(tmp_path, monkeypatch):
    """Filesystem tools with a RELATIVE WORKSPACE_DIR (the production default).

    chdir into a scratch dir and point WORKSPACE_DIR at a relative name, so
    task_paths.work_dir() returns a relative _work/ path — exactly the runtime
    condition that triggered the subdir-flatten bug.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKSPACE_DIR", "workspace_data")  # RELATIVE, like .env.example
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.mcp.tools.filesystem as _fs
    importlib.reload(_fs)
    yield _fs
    tp._parent_cache.clear()


def _set_task_context(task_id: str):
    """Make task context authoritative so writes resolve to this task's _work/."""
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    return current_task_id.set(task_id)


@pytest.mark.asyncio
async def test_write_preserves_subdirectory(fs_relative):
    """A write to ``a/b/x.py`` must land at ``_work/a/b/x.py``, not ``_work/x.py``."""
    from app.core.execution_context import current_task_id

    tok = _set_task_context("subdirtk")
    try:
        ws = fs_relative.get_task_workspace()
        res = await fs_relative.handle_write_file(
            {"path": "cuas_design/compute/x.py", "content": "print('hi')\n"}
        )
        assert res["success"] is True

        at_subdir = (ws / "cuas_design" / "compute" / "x.py").exists()
        at_root = (ws / "x.py").exists()

        assert at_subdir, "file must be written at the requested subdir path"
        assert not at_root, "file must NOT be flattened to _work/ root (the bug)"
        # The reported path must reflect the real on-disk subdir location.
        assert res["data"]["path"].replace("\\", "/").endswith(
            "cuas_design/compute/x.py"
        )
    finally:
        current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_write_root_file_still_works(fs_relative):
    """A plain root-level write keeps working (no regression)."""
    from app.core.execution_context import current_task_id

    tok = _set_task_context("roottk00")
    try:
        ws = fs_relative.get_task_workspace()
        res = await fs_relative.handle_write_file(
            {"path": "report.md", "content": "hello"}
        )
        assert res["success"] is True
        assert (ws / "report.md").exists()
    finally:
        current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_write_deep_nested_subdir_preserved(fs_relative):
    """A deeper nested path must also be preserved end-to-end."""
    from app.core.execution_context import current_task_id

    tok = _set_task_context("deeptk00")
    try:
        ws = fs_relative.get_task_workspace()
        res = await fs_relative.handle_write_file(
            {"path": "cuas_design/figures/sub/fig.txt", "content": "x"}
        )
        assert res["success"] is True
        assert (ws / "cuas_design" / "figures" / "sub" / "fig.txt").exists()
        assert not (ws / "fig.txt").exists()
    finally:
        current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_no_redirect_warning_when_path_honoured(fs_relative):
    """The normal (non-redirected) case must NOT emit a relocation warning — the
    file landed exactly where requested, so the short header is honest."""
    from app.core.execution_context import current_task_id

    tok = _set_task_context("hdrtk000")
    try:
        res = await fs_relative.handle_write_file(
            {"path": "cuas_design/report/x.md", "content": "body"}
        )
        assert res["success"] is True
        # data.path is the source of truth and must end with the requested subdir.
        assert res["data"]["path"].replace("\\", "/").endswith("cuas_design/report/x.md")
        # No relocation warning when the request was honoured verbatim.
        assert "relocated" not in res["output"].lower()
    finally:
        current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_redirect_to_root_is_surfaced_honestly(fs_relative, monkeypatch):
    """If a write is redirected away from the requested path (the fallback at
    handle_write_file that rewrites full_path to ws/<basename>), the agent MUST be
    told — silently reporting the requested path is the dishonesty behind the
    1f6decce phantom-write confusion.

    The redirect fires when _smart_resolve_path lands INSIDE WORKSPACE_DIR but
    OUTSIDE this task's _work/ (e.g. the workspace root). It must NOT be confused
    with a true escape outside WORKSPACE_DIR, which the access-check (B1 fix,
    relative_to) now correctly denies BEFORE the redirect branch."""
    from app.core.execution_context import current_task_id

    tok = _set_task_context("rdrtk000")
    try:
        ws = fs_relative.get_task_workspace()
        # Force the redirect branch with a path INSIDE WORKSPACE_DIR but outside the
        # task _work/ (the workspace root itself) — passes the access-check, but is
        # not under ws, so the fallback rewrites to ws/<basename>.
        inside_ws_outside_task = fs_relative.WORKSPACE_DIR.resolve() / "stray.md"
        monkeypatch.setattr(
            fs_relative, "_smart_resolve_path",
            lambda rel, w, root: inside_ws_outside_task,
        )
        res = await fs_relative.handle_write_file(
            {"path": "deep/sub/stray.md", "content": "body"}
        )
        assert res["success"] is True
        # File actually landed at ws/stray.md (basename), NOT deep/sub/stray.md.
        assert (ws / "stray.md").exists()
        # The output must HONESTLY surface that the location differs from requested.
        assert "relocated" in res["output"].lower()
        assert res["data"]["path"].replace("\\", "/").endswith("/stray.md")
    finally:
        current_task_id.reset(tok)
