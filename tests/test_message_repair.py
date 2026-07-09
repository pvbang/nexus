"""Tests for fix_orphan_tool_use — tool_use/tool_result reconciliation (audit C1/C3).

This repair is Bedrock/Anthropic-critical (a mismatch → provider 400) and had zero
tests before extraction. Cases: orphan tool_use gets a placeholder result, orphan
tool_result is dropped, both at once, valid history is untouched, placeholder lands
right after the assistant turn.
"""

from __future__ import annotations

from app.orchestration.message_repair import (
    _INTERRUPTED_PLACEHOLDER,
    fix_orphan_tool_use,
)


def _assistant(*ids):
    return {"role": "assistant", "tool_calls": [{"id": i} for i in ids]}


def _tool(tc_id, content="ok"):
    return {"role": "tool", "tool_call_id": tc_id, "content": content}


# ───────────────────────── no-op ─────────────────────────


def test_valid_history_returned_unchanged():
    msgs = [
        {"role": "user", "content": "hi"},
        _assistant("a1"),
        _tool("a1"),
    ]
    out = fix_orphan_tool_use(msgs)
    assert out is msgs  # same object — nothing to fix


def test_no_tools_at_all_is_noop():
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert fix_orphan_tool_use(msgs) is msgs


# ───────────────────────── orphan tool_use ─────────────────────────


def test_orphan_tool_use_gets_placeholder_result():
    msgs = [_assistant("a1")]  # tool_use with no result
    out = fix_orphan_tool_use(msgs)
    assert len(out) == 2
    assert out[1]["role"] == "tool"
    assert out[1]["tool_call_id"] == "a1"
    assert out[1]["content"] == _INTERRUPTED_PLACEHOLDER


def test_placeholder_inserted_right_after_assistant():
    msgs = [_assistant("a1"), {"role": "user", "content": "next"}]
    out = fix_orphan_tool_use(msgs)
    # Order: assistant, placeholder-tool, user
    assert [m["role"] for m in out] == ["assistant", "tool", "user"]
    assert out[1]["tool_call_id"] == "a1"


def test_partial_results_only_missing_one_filled():
    msgs = [_assistant("a1", "a2"), _tool("a1")]  # a2 missing
    out = fix_orphan_tool_use(msgs)
    tool_ids = [m["tool_call_id"] for m in out if m["role"] == "tool"]
    assert set(tool_ids) == {"a1", "a2"}
    # a2 is the placeholder, a1 keeps its real result.
    a2 = next(m for m in out if m.get("tool_call_id") == "a2")
    assert a2["content"] == _INTERRUPTED_PLACEHOLDER


# ───────────────────────── orphan tool_result ─────────────────────────


def test_orphan_tool_result_is_dropped():
    msgs = [
        _assistant("a1"),
        _tool("a1"),
        _tool("ghost"),  # references no tool_use
    ]
    out = fix_orphan_tool_use(msgs)
    ids = [m.get("tool_call_id") for m in out if m["role"] == "tool"]
    assert "ghost" not in ids
    assert "a1" in ids


# ───────────────────────── both directions ─────────────────────────


def test_both_orphan_use_and_orphan_result():
    msgs = [
        _assistant("a1"),   # use without result → placeholder added
        _tool("ghost"),     # result without use → dropped
    ]
    out = fix_orphan_tool_use(msgs)
    tool_ids = [m["tool_call_id"] for m in out if m["role"] == "tool"]
    assert tool_ids == ["a1"]
    assert out[1]["content"] == _INTERRUPTED_PLACEHOLDER


def test_handles_missing_keys_defensively():
    # Assistant without tool_calls, tool without tool_call_id — must not raise.
    msgs = [{"role": "assistant"}, {"role": "tool"}]
    out = fix_orphan_tool_use(msgs)
    assert isinstance(out, list)
