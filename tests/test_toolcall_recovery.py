"""Unit tests for the pure tool-call recovery module (audit 2026-06-29).

These pin the JSON-adjacency + XML-invoke recovery behavior directly against the
public entry point, so the logic is tested in one place regardless of which
call site (llm_client boundary, or agent_worker F2 net) invokes it.
"""

from __future__ import annotations

import json

from app.services.toolcall_recovery import recover_tool_calls

_VALID = {"write_file", "edit_file", "read_file"}


def _paths(calls):
    return sorted(json.loads(c["function"]["arguments"]).get("path") for c in calls)


def test_real_xml_invoke_leak_recovered():
    content = (
        '<invoke name="write_file">\n'
        '<parameter name="path">r.md</parameter>\n'
        '<parameter name="content">hi</parameter>\n'
        "</invoke>"
    )
    calls = recover_tool_calls(content, _VALID)
    assert len(calls) == 1 and _paths(calls) == ["r.md"]


def test_content_with_inner_fence_preserved():
    content = (
        '<invoke name="write_file">'
        '<parameter name="path">s.py</parameter>'
        '<parameter name="content">import x\n```python\ndemo=1\n```\nprint(demo)</parameter>'
        "</invoke>"
    )
    body = json.loads(recover_tool_calls(content, _VALID)[0]["function"]["arguments"])["content"]
    assert "demo=1" in body and "print(demo)" in body


def test_doc_fence_closed_dropped():
    content = "ex:\n```xml\n<invoke name=\"write_file\"><parameter name=\"path\">no.md</parameter></invoke>\n```\nend"
    assert recover_tool_calls(content, _VALID) == []


def test_doc_fence_unclosed_dropped():
    content = "ex:\n```\n<invoke name=\"write_file\"><parameter name=\"path\">no.md</parameter></invoke>"
    assert recover_tool_calls(content, _VALID) == []


def test_glued_close_then_real_invoke_recovered():
    content = (
        "```python\nprint(1)```\n"
        '<invoke name="write_file"><parameter name="path">real.md</parameter>'
        '<parameter name="content">x</parameter></invoke>'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["real.md"]


def test_inline_backtick_before_real_invoke_recovered():
    content = (
        "use ``` to open a fence\n"
        '<invoke name="write_file"><parameter name="path">real.md</parameter>'
        '<parameter name="content">x</parameter></invoke>'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["real.md"]


def test_two_invokes_first_content_odd_backtick():
    content = (
        '<invoke name="write_file"><parameter name="path">a.md</parameter>'
        '<parameter name="content">use ``` here</parameter></invoke>\n'
        '<invoke name="write_file"><parameter name="path">b.md</parameter>'
        '<parameter name="content">x</parameter></invoke>'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["a.md", "b.md"]


def test_json_adjacent_leak_recovered():
    content = (
        'I will write it.\n'
        '{"name": "write_file", "arguments": {"path": "j.md", "content": "body"}}\n'
        "</tool_call>"
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["j.md"]


def test_ascii_pipe_token_adjacent_json_recovered():
    # A model whose chat template wraps the call in ASCII <|tool_call|> leaks the
    # JSON next to that delimiter; adjacency lets recovery pick it up.
    content = (
        "I will write it.\n"
        "<|tool_call|>\n"
        '{"name": "write_file", "arguments": {"path": "p.md", "content": "x"}}'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["p.md"]


def test_fullwidth_pipe_token_adjacent_json_recovered():
    # DeepSeek/Qwen-style fullwidth bar (U+FF5C) wrapping. When a rotation model
    # is served through a proxy that does NOT normalize the special token into a
    # native tool_calls slot, the JSON still arrives adjacent to the delimiter —
    # recovery must catch it so the agent acts instead of "thinking only".
    content = (
        "Tôi sẽ ghi file.\n"
        "<｜tool▁call｜>\n"
        '{"name": "write_file", "arguments": {"path": "q.md", "content": "x"}}'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["q.md"]


def test_unknown_tool_rejected():
    content = '<invoke name="rm_rf"><parameter name="p">/</parameter></invoke>'
    assert recover_tool_calls(content, _VALID) == []


def test_empty_inputs_safe():
    assert recover_tool_calls("", _VALID) == []
    assert recover_tool_calls("just prose, no tool call", _VALID) == []
    assert recover_tool_calls("<invoke name=\"write_file\"></invoke>", set()) == []
