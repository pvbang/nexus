"""Boundary recovery in LLMClient._do_chat_call (audit 2026-06-29).

When a native provider returns no tool_calls but leaked one into `content`,
_do_chat_call recovers it so downstream sees a uniform shape. Gated on a
non-safety finish_reason and the presence of `tools`.
"""

from __future__ import annotations

import json
import types

import pytest

from app.services.llm_client import LLMClient


def _fake_response(content, tool_calls=None, finish_reason="stop"):
    """Build an object shaped like an openai/litellm ChatCompletion response."""
    msg = types.SimpleNamespace(
        content=content, role="assistant", tool_calls=tool_calls, reasoning=None
    )
    choice = types.SimpleNamespace(message=msg, finish_reason=finish_reason)
    usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return types.SimpleNamespace(choices=[choice], usage=usage)


_WRITE_TOOL = {
    "type": "function",
    "function": {"name": "write_file", "parameters": {"type": "object", "properties": {}}},
}
_XML_LEAK = (
    '<invoke name="write_file">'
    '<parameter name="path">report/x.md</parameter>'
    '<parameter name="content">body</parameter>'
    "</invoke>"
)


def _client(monkeypatch, response):
    """An LLMClient whose underlying call returns `response`. litellm import is
    forced to fail so _do_chat_call uses the openai-client branch we stub."""
    client = LLMClient(provider="bedrock", model="anthropic.claude-opus-4-8")

    async def _fake_create(**kwargs):
        return response

    # Stub the openai-compat path; force the litellm branch to ImportError.
    monkeypatch.setattr(client.client.chat.completions, "create", _fake_create)
    import builtins
    real_import = builtins.__import__

    def _no_litellm(name, *a, **k):
        if name == "litellm":
            raise ImportError("forced for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_litellm)
    return client


async def test_xml_leak_recovered_at_boundary(monkeypatch):
    client = _client(monkeypatch, _fake_response(_XML_LEAK, tool_calls=None))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=[_WRITE_TOOL],
        tool_choice=None, response_format=None,
    )
    assert result.get("tool_calls"), "an XML-invoke leak was not recovered at the boundary"
    args = json.loads(result["tool_calls"][0]["function"]["arguments"])
    assert args["path"] == "report/x.md"


async def test_real_tool_calls_not_double_recovered(monkeypatch):
    real = [types.SimpleNamespace(
        id="c1", type="function",
        function=types.SimpleNamespace(name="write_file", arguments='{"path": "a.md"}'),
    )]
    client = _client(monkeypatch, _fake_response("ignored prose", tool_calls=real))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=[_WRITE_TOOL],
        tool_choice=None, response_format=None,
    )
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "c1"  # untouched, not re-derived


async def test_safety_finish_blocks_recovery(monkeypatch):
    client = _client(monkeypatch, _fake_response(_XML_LEAK, tool_calls=None,
                                                 finish_reason="content_filter"))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=[_WRITE_TOOL],
        tool_choice=None, response_format=None,
    )
    assert not result.get("tool_calls"), "a content-filtered turn must not recover a tool call"


async def test_no_tools_means_no_recovery(monkeypatch):
    client = _client(monkeypatch, _fake_response(_XML_LEAK, tool_calls=None))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=None,
        tool_choice=None, response_format=None,
    )
    assert not result.get("tool_calls"), "no tools passed → no valid_names → no recovery"
