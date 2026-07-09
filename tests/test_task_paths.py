"""Tests for the central task-path resolver (app.core.task_paths).

Covers flat top-level tasks, nested subtasks, the shared-_work invariant,
dual-location lookup during transition, and parent resolution via
contextvar / cache / .task_meta.json.
"""

import importlib
import json

import pytest


@pytest.fixture
def task_paths(tmp_path, monkeypatch):
    """Reload task_paths against a temp WORKSPACE_DIR for isolation."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    # Clear any memoized parents leaking across reloads.
    tp._parent_cache.clear()
    yield tp
    tp._parent_cache.clear()


def test_dir_name_truncates_to_8(task_paths):
    assert task_paths.dir_name("a1b2c3d4e5f6") == "a1b2c3d4"
    assert task_paths.dir_name("short") == "short"


def test_top_level_task_is_flat(task_paths):
    d = task_paths.task_dir("a1b2c3d4ffff")
    assert d == task_paths.tasks_root() / "a1b2c3d4"


def test_context_and_work_for_top_level(task_paths):
    tid = "a1b2c3d4ffff"
    ctx = task_paths.context_dir(tid)
    work = task_paths.work_dir(tid)
    assert ctx == task_paths.tasks_root() / "a1b2c3d4" / "_context"
    assert work == task_paths.tasks_root() / "a1b2c3d4" / "_work"
    assert ctx.exists() and work.exists()


def test_subtask_nested_under_parent(task_paths):
    parent, child = "11111111aaaa", "22222222bbbb"
    task_paths.register_parent(child, parent)
    d = task_paths.task_dir(child)
    assert d == task_paths.tasks_root() / "11111111" / "subtasks" / "22222222"


def test_subtask_work_is_shared_with_parent(task_paths):
    """The core invariant: subtask _work resolves to the PARENT's _work."""
    parent, child = "11111111aaaa", "22222222bbbb"
    task_paths.register_parent(child, parent)
    assert task_paths.work_dir(child) == task_paths.tasks_root() / "11111111" / "_work"
    # But its _context is nested (its own).
    assert task_paths.context_dir(child) == (
        task_paths.tasks_root() / "11111111" / "subtasks" / "22222222" / "_context"
    )


def test_multilevel_descendants_nest_under_root(task_paths):
    """A→B→C→D delegation chain: EVERY descendant lives one level under the
    root conversation folder, never creating new top-level folders."""
    A, B, C, D = "aaaaaaaa1111", "bbbbbbbb2222", "cccccccc3333", "dddddddd4444"
    task_paths.register_parent(A, None)
    task_paths.register_parent(B, A)
    task_paths.register_parent(C, B)
    task_paths.register_parent(D, C)

    root = task_paths.tasks_root()
    assert task_paths.task_dir(A) == root / "aaaaaaaa"
    assert task_paths.task_dir(B) == root / "aaaaaaaa" / "subtasks" / "bbbbbbbb"
    assert task_paths.task_dir(C) == root / "aaaaaaaa" / "subtasks" / "cccccccc"
    assert task_paths.task_dir(D) == root / "aaaaaaaa" / "subtasks" / "dddddddd"


def test_multilevel_descendants_share_root_work(task_paths):
    """_work of every descendant resolves to the ROOT's _work (one codebase)."""
    A, B, C, D = "aaaaaaaa1111", "bbbbbbbb2222", "cccccccc3333", "dddddddd4444"
    task_paths.register_parent(A, None)
    task_paths.register_parent(B, A)
    task_paths.register_parent(C, B)
    task_paths.register_parent(D, C)

    root_work = task_paths.tasks_root() / "aaaaaaaa" / "_work"
    assert task_paths.work_dir(A) == root_work
    assert task_paths.work_dir(B) == root_work
    assert task_paths.work_dir(C) == root_work
    assert task_paths.work_dir(D) == root_work


def test_get_root_walks_chain(task_paths):
    A, B, C = "aaaaaaaa1111", "bbbbbbbb2222", "cccccccc3333"
    task_paths.register_parent(A, None)
    task_paths.register_parent(B, A)
    task_paths.register_parent(C, B)
    assert task_paths.get_root(A) == A
    assert task_paths.get_root(B) == A
    assert task_paths.get_root(C) == A


def test_get_root_breaks_cycle(task_paths):
    """A corrupt parent cycle must not hang — resolves to a stable answer."""
    X, Y = "xxxxxxxx1111", "yyyyyyyy2222"
    task_paths.register_parent(X, Y)
    task_paths.register_parent(Y, X)
    # Must terminate (no infinite loop) and return one of the two.
    assert task_paths.get_root(X) in (X, Y)


def test_register_parent_ignores_self(task_paths):
    task_paths.register_parent("33333333", "33333333")
    assert task_paths.get_parent("33333333") is None


def test_get_parent_from_meta_survives_no_cache(task_paths):
    """Parent must be recoverable from .task_meta.json (restart survival)."""
    parent, child = "44444444aaaa", "55555555bbbb"
    flat = task_paths.tasks_root() / "55555555"
    flat.mkdir(parents=True, exist_ok=True)
    (flat / ".task_meta.json").write_text(
        json.dumps({"task_id": child, "parent_task_id": parent}), encoding="utf-8"
    )
    # Cold cache — must read from disk.
    task_paths._parent_cache.clear()
    assert task_paths.get_parent(child) == parent


def test_get_parent_from_nested_meta_survives_restart(task_paths):
    """A subtask's meta lives at the NESTED path; parent must be recoverable
    from a cold cache (regression: flat-only read missed nested meta)."""
    import json
    parent, child = "aaaa1111ffff", "bbbb2222ffff"
    task_paths.register_parent(child, parent)
    nested = task_paths.task_dir(child)
    nested.mkdir(parents=True, exist_ok=True)
    (nested / ".task_meta.json").write_text(
        json.dumps({"task_id": child, "parent_task_id": parent}), encoding="utf-8"
    )
    # Simulate process restart: cache empty, must rediscover via nested meta scan.
    task_paths._parent_cache.clear()
    assert task_paths.get_parent(child) == parent


def test_find_task_dir_legacy_flat(task_paths):
    flat = task_paths.tasks_root() / "66666666"
    (flat / "_context").mkdir(parents=True, exist_ok=True)
    assert task_paths.find_task_dir("66666666ffff") == flat


def test_find_task_dir_nested(task_paths):
    parent, child = "77777777aaaa", "88888888bbbb"
    task_paths.register_parent(child, parent)
    nested = task_paths.subtask_dir(parent, child)
    (nested / "_context").mkdir(parents=True, exist_ok=True)
    assert task_paths.find_task_dir(child) == nested


def test_find_task_dir_missing_returns_none(task_paths):
    assert task_paths.find_task_dir("deadbeefdead") is None


def test_find_task_dir_scans_for_orphan_subtask(task_paths):
    """A subtask whose parent isn't cached should still be found by scan."""
    parent, child = "99999999aaaa", "aaaaaaaabbbb"
    nested = task_paths.tasks_root() / "99999999" / "subtasks" / "aaaaaaaa"
    nested.mkdir(parents=True, exist_ok=True)
    task_paths._parent_cache.clear()
    found = task_paths.find_task_dir(child)
    assert found == nested
    # Scan should have memoized the parent.
    assert task_paths.get_parent(child) == "99999999"
