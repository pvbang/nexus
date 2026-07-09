"""Tests for the unified edit_file tool — all five modes + safety guards.

Pure-logic helpers (_apply_*, _find_fuzzy_spans, _suggest_near_miss) are tested
directly. The full handler is exercised against a temp WORKSPACE_DIR so the
atomic-write / transactional path is covered end to end.
"""

from __future__ import annotations

import importlib

import pytest

from app.mcp.tools.file_edit import (
    _apply_anchor_edit,
    _apply_line_edit,
    _apply_regex_edit,
    _apply_single_edit,
    _find_fuzzy_spans,
    _suggest_near_miss,
)


# ──────────────────────────────────────────────────────────────────
# String mode + whitespace-tolerant fallback
# ──────────────────────────────────────────────────────────────────


def test_string_exact_match():
    content = "alpha\nbeta\ngamma\n"
    new, err, n = _apply_single_edit(content, "beta", "BETA", 1)
    assert err is None
    assert new == "alpha\nBETA\ngamma\n"
    assert n == 1


def test_string_unique_required_when_multiple():
    content = "x = 1\nx = 1\n"
    new, err, n = _apply_single_edit(content, "x = 1", "x = 2", 1)
    assert new is None
    assert "must be unique" in err


def test_string_occurrence_all():
    content = "a\na\na\n"
    new, err, n = _apply_single_edit(content, "a", "b", 0)
    assert err is None
    assert new == "b\nb\nb\n"
    assert n == 3


def test_string_nth_occurrence():
    content = "a\na\na\n"
    new, err, n = _apply_single_edit(content, "a", "Z", 2)
    assert err is None
    assert new == "a\nZ\na\n"
    assert n == 1


def test_fuzzy_fallback_on_indentation_drift():
    # old_string has different indentation than the file, but lines match stripped.
    content = "def f():\n        return 1\n"
    old = "def f():\n    return 1"  # 4 spaces vs 8 on disk
    new, err, n = _apply_single_edit(content, old, "def f():\n    return 2", 1)
    assert err is None, err
    assert "return 2" in new
    assert n == 1


def test_find_fuzzy_spans_basic():
    content = "    line one\n    line two\n"
    spans = _find_fuzzy_spans(content, "line one\nline two")
    assert len(spans) == 1
    start, end = spans[0]
    assert content[start:end] == "    line one\n    line two"


def test_near_miss_hint_present_on_failure():
    content = "the quick brown fox\njumps over\n"
    new, err, n = _apply_single_edit(content, "the quick brown dog", "x", 1)
    assert new is None
    assert "Closest region" in err
    assert "line 1" in err


# ──────────────────────────────────────────────────────────────────
# Regex mode
# ──────────────────────────────────────────────────────────────────


def test_regex_replace_all_with_groups():
    content = "v1 = 10\nv2 = 20\n"
    new, err, n = _apply_regex_edit(content, r"v(\d+) = (\d+)", r"var\1 = \2", 0)
    assert err is None
    assert new == "var1 = 10\nvar2 = 20\n"
    assert n == 2


def test_regex_nth_occurrence():
    content = "x x x"
    new, err, n = _apply_regex_edit(content, r"x", "Y", 2)
    assert err is None
    assert new == "x Y x"
    assert n == 1


def test_regex_no_match():
    new, err, n = _apply_regex_edit("abc", r"\d+", "N", 0)
    assert new is None
    assert "matched nothing" in err


def test_regex_invalid_pattern():
    new, err, n = _apply_regex_edit("abc", r"(unclosed", "x", 0)
    assert new is None
    assert "Invalid regex" in err


# ──────────────────────────────────────────────────────────────────
# Line-span mode (with expected_text guard)
# ──────────────────────────────────────────────────────────────────


def test_line_replace_with_correct_expected():
    content = "one\ntwo\nthree\nfour\n"
    new, err, n = _apply_line_edit(content, 2, 3, "TWO\nTHREE", "two\nthree")
    assert err is None
    assert new == "one\nTWO\nTHREE\nfour\n"
    assert n == 1


def test_line_delete_span():
    content = "one\ntwo\nthree\nfour\n"
    new, err, n = _apply_line_edit(content, 2, 3, "", "two\nthree")
    assert err is None
    assert new == "one\nfour\n"


def test_line_rejects_mismatched_expected():
    content = "one\ntwo\nthree\n"
    new, err, n = _apply_line_edit(content, 2, 2, "X", "WRONG")
    assert new is None
    assert "does not match" in err


def test_line_requires_expected_text():
    content = "one\ntwo\n"
    new, err, n = _apply_line_edit(content, 1, 1, "X", None)
    assert new is None
    assert "requires 'expected_text'" in err


def test_line_invalid_range():
    new, err, n = _apply_line_edit("a\nb\n", 5, 2, "x", "a")
    assert new is None
    assert "Invalid line range" in err


# ──────────────────────────────────────────────────────────────────
# Anchor-insert mode
# ──────────────────────────────────────────────────────────────────


def test_anchor_after_keeps_marker():
    content = "import os\n\ndef main():\n    pass\n"
    new, err, n = _apply_anchor_edit(content, "import os\n", None, "import sys\n", 1)
    assert err is None
    assert new == "import os\nimport sys\n\ndef main():\n    pass\n"


def test_anchor_before_keeps_marker():
    content = "def main():\n    pass\n"
    new, err, n = _apply_anchor_edit(content, None, "def main():", "# entry\n", 1)
    assert err is None
    assert new.startswith("# entry\ndef main():")


def test_anchor_not_found():
    new, err, n = _apply_anchor_edit("abc", "xyz", None, "q", 1)
    assert new is None
    assert "Anchor text not found" in err


def test_anchor_ambiguous_requires_unique():
    content = "a\na\n"
    new, err, n = _apply_anchor_edit(content, "a", None, "Z", 1)
    assert new is None
    assert "must be unique" in err


# ──────────────────────────────────────────────────────────────────
# Full handler integration (temp workspace)
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def edit_env(tmp_path, monkeypatch):
    """Point WORKSPACE_DIR at tmp and reload modules that cache it."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.mcp.tools.filesystem as fs
    importlib.reload(fs)
    import app.mcp.tools.file_edit as fe
    importlib.reload(fe)
    return fe, tmp_path


async def test_handler_string_edit(edit_env):
    fe, tmp = edit_env
    target = tmp / "f.txt"
    target.write_text("hello world\n", encoding="utf-8")
    res = await fe.handle_edit_file(
        {"path": "f.txt", "old_string": "world", "new_string": "there"}
    )
    assert res["success"], res["output"]
    assert target.read_text(encoding="utf-8") == "hello there\n"


async def test_handler_batch_atomic_rollback(edit_env):
    fe, tmp = edit_env
    target = tmp / "f.txt"
    target.write_text("a\nb\nc\n", encoding="utf-8")
    # Second edit fails → whole batch must roll back, file unchanged.
    res = await fe.handle_edit_file(
        {
            "path": "f.txt",
            "edits": [
                {"old_string": "a", "new_string": "A"},
                {"old_string": "NOPE", "new_string": "X"},
            ],
        }
    )
    assert not res["success"]
    assert target.read_text(encoding="utf-8") == "a\nb\nc\n"


async def test_handler_regex_mode(edit_env):
    fe, tmp = edit_env
    target = tmp / "f.txt"
    target.write_text("color colour\n", encoding="utf-8")
    res = await fe.handle_edit_file(
        {"path": "f.txt", "pattern": r"colou?r", "replacement": "COLOR", "occurrence": 0}
    )
    assert res["success"], res["output"]
    assert target.read_text(encoding="utf-8") == "COLOR COLOR\n"


async def test_handler_line_mode(edit_env):
    fe, tmp = edit_env
    target = tmp / "f.txt"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    res = await fe.handle_edit_file(
        {
            "path": "f.txt",
            "start_line": 2,
            "end_line": 2,
            "expected_text": "two",
            "new_string": "TWO",
        }
    )
    assert res["success"], res["output"]
    assert target.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


async def test_handler_anchor_mode(edit_env):
    fe, tmp = edit_env
    target = tmp / "f.txt"
    target.write_text("import os\n", encoding="utf-8")
    res = await fe.handle_edit_file(
        {"path": "f.txt", "after": "import os\n", "new_string": "import sys\n"}
    )
    assert res["success"], res["output"]
    assert target.read_text(encoding="utf-8") == "import os\nimport sys\n"


async def test_handler_dry_run_does_not_write(edit_env):
    fe, tmp = edit_env
    target = tmp / "f.txt"
    target.write_text("keep me\n", encoding="utf-8")
    res = await fe.handle_edit_file(
        {"path": "f.txt", "old_string": "keep me", "new_string": "changed", "dry_run": True}
    )
    assert res["success"]
    assert "[DRY RUN]" in res["output"]
    assert target.read_text(encoding="utf-8") == "keep me\n"


async def test_handler_no_edit_params_errors(edit_env):
    fe, tmp = edit_env
    (tmp / "f.txt").write_text("x\n", encoding="utf-8")
    res = await fe.handle_edit_file({"path": "f.txt"})
    assert not res["success"]
    assert "No edit specified" in res["output"]
