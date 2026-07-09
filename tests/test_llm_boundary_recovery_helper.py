"""Shared boundary tool-call recovery helper (E9).

`_do_chat_call` recovered tool calls leaked into prose `content`, but the
streaming path `chat_streaming_with_tools` did NOT — a model that leaks a
`<tool_call>` while streaming would phantom-write. Rather than duplicate the
guarded recovery block (which would drift), both paths call one helper:
`LLMClient._recover_leaked_tool_calls(result, content, tools)`.

The helper mutates `result["tool_calls"]` in place when it recovers a call, and
is a no-op when: tool_calls already present, no content, no tools offered, or the
turn was content-filtered (safety finish_reason — never execute filtered prose).
"""

from __future__ import annotations

from app.services.llm_client import LLMClient

_PHANTOM = (
    "I'll write the file now.\n"
    "<tool_call>\n"
    '{"name": "write_file", "arguments": {"path": "r.md", "content": "hi"}}\n'
    "</tool_call>"
)
_TOOLS = [{"function": {"name": "write_file"}}, {"function": {"name": "read_file"}}]


def test_recovers_leaked_call_into_empty_slot():
    result = {"content": _PHANTOM, "finish_reason": "stop"}
    LLMClient._recover_leaked_tool_calls(result, _PHANTOM, _TOOLS)
    assert result.get("tool_calls"), "a leaked <tool_call> must be recovered"
    assert result["tool_calls"][0]["function"]["name"] == "write_file"


def test_noop_when_tool_calls_already_present():
    existing = [{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}]
    result = {"content": _PHANTOM, "finish_reason": "stop", "tool_calls": existing}
    LLMClient._recover_leaked_tool_calls(result, _PHANTOM, _TOOLS)
    assert result["tool_calls"] is existing  # untouched


def test_noop_without_tools():
    result = {"content": _PHANTOM, "finish_reason": "stop"}
    LLMClient._recover_leaked_tool_calls(result, _PHANTOM, None)
    assert not result.get("tool_calls")


def test_noop_on_safety_finish_reason():
    # A content-filtered turn must never have its prose executed.
    result = {"content": _PHANTOM, "finish_reason": "content_filter"}
    LLMClient._recover_leaked_tool_calls(result, _PHANTOM, _TOOLS)
    assert not result.get("tool_calls")


def test_noop_on_plain_prose():
    text = "I analysed the design and it looks fine. No tool needed."
    result = {"content": text, "finish_reason": "stop"}
    LLMClient._recover_leaked_tool_calls(result, text, _TOOLS)
    assert not result.get("tool_calls")
