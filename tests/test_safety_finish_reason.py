"""Provider safety-stops must not leave executable tool_calls behind."""

from app.services.safety_finish_reason import (
    sanitize_safety_finish,
    SAFETY_FINISH_REASONS,
)


def test_content_filter_strips_tool_calls():
    result = {
        "content": "",
        "finish_reason": "content_filter",
        "tool_calls": [{"id": "1", "type": "function",
                        "function": {"name": "run", "arguments": '{"x":'}}],
    }
    out = sanitize_safety_finish(result)
    assert "tool_calls" not in out or not out["tool_calls"]
    assert out["_safety_stripped"] is True
    assert out["content"]  # non-empty explanatory text


def test_refusal_without_tool_calls_only_flags():
    result = {"content": "I can't help with that.", "finish_reason": "refusal"}
    out = sanitize_safety_finish(result)
    assert out["_safety_stripped"] is True
    assert out["content"] == "I can't help with that."


def test_normal_stop_untouched():
    result = {
        "content": "done",
        "finish_reason": "stop",
        "tool_calls": [{"id": "1", "type": "function",
                        "function": {"name": "run", "arguments": "{}"}}],
    }
    out = sanitize_safety_finish(result)
    assert out["tool_calls"]  # preserved
    assert "_safety_stripped" not in out


def test_tool_calls_finish_reason_untouched():
    result = {
        "content": "",
        "finish_reason": "tool_calls",
        "tool_calls": [{"id": "1", "type": "function",
                        "function": {"name": "run", "arguments": "{}"}}],
    }
    out = sanitize_safety_finish(result)
    assert out["tool_calls"]


def test_case_insensitive_reason():
    assert "content_filter" in SAFETY_FINISH_REASONS
    result = {"content": "", "finish_reason": "CONTENT_FILTER",
              "tool_calls": [{"id": "1", "type": "function",
                              "function": {"name": "x", "arguments": "{}"}}]}
    out = sanitize_safety_finish(result)
    assert not out.get("tool_calls")
