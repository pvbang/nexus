"""Tests for orchestrator read-loop dedup keys (audit #4: reduce "rề rà").

The orchestrator's evaluation read-loop re-ran the same workspace reads across
rounds, burning LLM round-trips. _read_dedup_key gives each read a stable key so
the loop can skip repeats. These pin the keying rules.
"""

from __future__ import annotations

from app.orchestration.orchestrator_tools import _read_dedup_key


def test_same_file_path_same_key():
    a = _read_dedup_key("read_workspace_file", {"path": "_work/app.py"})
    b = _read_dedup_key("read_workspace_file", {"path": "_work/app.py"})
    assert a == b


def test_path_key_is_case_and_space_insensitive():
    a = _read_dedup_key("read_workspace_file", {"path": "_work/App.py"})
    b = _read_dedup_key("read_workspace_file", {"path": "  _work/app.py  "})
    assert a == b


def test_different_files_different_keys():
    a = _read_dedup_key("read_workspace_file", {"path": "_work/a.py"})
    b = _read_dedup_key("read_workspace_file", {"path": "_work/b.py"})
    assert a != b


def test_search_keyed_by_query():
    a = _read_dedup_key("search_workspace", {"query": "TODO"})
    b = _read_dedup_key("search_workspace", {"query": "todo"})
    assert a == b
    c = _read_dedup_key("search_workspace", {"query": "FIXME"})
    assert a != c


def test_workspace_tree_collapses_to_single_key():
    # Tree takes no meaningful arg — any two tree calls are the same read.
    a = _read_dedup_key("workspace_tree", {})
    b = _read_dedup_key("workspace_tree", {"ignored": "x"})
    assert a == b


def test_tool_name_namespaced():
    # Same arg under different tools must not collide.
    f = _read_dedup_key("read_workspace_file", {"path": "x"})
    s = _read_dedup_key("search_workspace", {"query": "x"})
    assert f != s
