"""B1 (audit 2026-06-29): workspace-boundary checks must compare PATH COMPONENTS,
not string prefixes.

``workspace_data`` is a string prefix of ``workspace_data_evil``, so a
``str.startswith`` boundary check classified a sibling directory
``workspace_data_evil/`` as "inside the workspace". An absolute path into such a
sibling then passed the safety gate. The fix compares resolved paths by component
(relative_to / commonpath), so only true descendants of the workspace are allowed.
"""

from __future__ import annotations

import os

from app.core.command_guard import check_path_safety


def test_sibling_dir_with_shared_prefix_is_outside_workspace():
    """An absolute path into a sibling dir sharing the workspace name prefix is
    NOT inside the workspace."""
    ws = os.path.abspath("workspace_data")
    sibling = os.path.abspath("workspace_data_evil") + "/secret.txt"
    verdict = check_path_safety(sibling, workspace_root="workspace_data")
    assert verdict.safe is False, (
        "a sibling dir sharing the workspace name prefix was wrongly judged inside "
        "the workspace (str.startswith boundary bug)"
    )
    # Sanity: the prefix trap that caused it.
    assert os.path.abspath("workspace_data_evil").startswith(ws)


def test_genuine_descendant_is_inside_workspace():
    """A real descendant of the workspace stays allowed (no over-blocking)."""
    inside = os.path.abspath("workspace_data") + "/tasks/t1/_work/out.md"
    verdict = check_path_safety(inside, workspace_root="workspace_data")
    assert verdict.safe is True


def test_workspace_dir_itself_is_inside():
    """The workspace root path itself is within the workspace."""
    root = os.path.abspath("workspace_data")
    verdict = check_path_safety(root, workspace_root="workspace_data")
    assert verdict.safe is True


def test_unrelated_absolute_path_is_outside():
    """A clearly unrelated absolute path is rejected (regression guard)."""
    verdict = check_path_safety("/etc/shadow", workspace_root="workspace_data")
    assert verdict.safe is False


def test_relative_path_still_allowed():
    """A normal relative path (no traversal) is still allowed."""
    verdict = check_path_safety("tasks/t1/_work/app.py", workspace_root="workspace_data")
    assert verdict.safe is True
