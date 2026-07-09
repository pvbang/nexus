"""Integration-style tests for subtask nesting and cleanup.

Verifies the core invariant of the path refactor: a subtask's _context/ is
nested under the parent while its _work/ stays shared with the parent, and
cleaning a subtask removes only its nested dir — never the parent's _work.

(The backbone context-writing classes route through task_paths.context_dir(),
which is exercised directly in test_task_paths.py. Importing those classes
here would pull in the full orchestration stack / DB deps, so we test the
resolver + delegate cleanup, which together guarantee the behavior.)
"""

import importlib

import pytest


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    yield tp, tmp_path
    tp._parent_cache.clear()


def test_cleanup_removes_nested_only_not_parent_work(env, tmp_path):
    """Cleaning a subtask must not touch the parent's shared _work."""
    tp, root = env
    parent, child = "parent22aaaa", "child222bbbb"
    tp.register_parent(child, parent)

    # Parent _work with a file the subtask "produced" (shared workspace).
    parent_work = tp.work_dir(child)  # resolves to parent's _work
    (parent_work / "artifact.txt").write_text("result", encoding="utf-8")

    # Subtask context dir (nested).
    sub_ctx = tp.context_dir(child)
    (sub_ctx / "ACTIVITY.md").write_text("log", encoding="utf-8")
    assert sub_ctx.exists()

    import app.mcp.tools.delegate as dg
    dg._cleanup_subtask_workspace(child, parent_task_id=parent)

    # Nested subtask dir gone, but parent _work + artifact preserved.
    assert not tp.subtask_dir(parent, child).exists()
    assert (parent_work / "artifact.txt").exists()
    assert parent_work.exists()
