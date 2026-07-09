"""Tests for prompted (text-based) tool-calling fallback.

Covers free/weak LLM providers that don't support OpenAI-style native
function-calling: tools are described in the prompt and the model's tool
calls are parsed back out of its text response.
"""

import json

import pytest

from app.services.prompted_tools import (
    build_tool_instructions,
    flatten_tool_messages,
    parse_tool_calls_from_text,
    supports_native_tools,
)


# Sample tool schemas in OpenAI/registry format.
SAMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "description": "Max results"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Run a shell command in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
]


# ── supports_native_tools ────────────────────────────────────────────────


def test_native_providers_supported():
    assert supports_native_tools("bedrock", "us.anthropic.claude-opus-4-6-v1")
    assert supports_native_tools("openai", "gpt-4o")
    assert supports_native_tools("openrouter", "anthropic/claude-sonnet-4")


def test_prompted_providers_not_native():
    assert not supports_native_tools("g4f", "gpt-4o")
    assert not supports_native_tools("pollinations", "openai")


def test_provider_case_insensitive():
    assert not supports_native_tools("G4F", "x")
    assert supports_native_tools("OpenAI", "gpt-4o")


# ── build_tool_instructions ──────────────────────────────────────────────


def test_instructions_list_every_tool():
    text = build_tool_instructions(SAMPLE_TOOLS)
    assert "web_search" in text
    assert "execute_command" in text


def test_instructions_describe_call_format():
    text = build_tool_instructions(SAMPLE_TOOLS)
    # Must teach the model the exact fenced block to emit.
    assert "tool_call" in text
    assert "arguments" in text


def test_instructions_include_required_params():
    text = build_tool_instructions(SAMPLE_TOOLS)
    assert "query" in text
    assert "command" in text


def test_instructions_empty_for_no_tools():
    assert build_tool_instructions([]) == ""


# ── parse_tool_calls_from_text ───────────────────────────────────────────

VALID = {"web_search", "execute_command"}


def test_parse_fenced_block():
    content = (
        "I'll search for that.\n"
        "```tool_call\n"
        '{"name": "web_search", "arguments": {"query": "bounty platforms"}}\n'
        "```\n"
    )
    calls = parse_tool_calls_from_text(content, VALID)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "web_search"
    # arguments must be a JSON STRING to match native tool_calls format
    args = json.loads(calls[0]["function"]["arguments"])
    assert args == {"query": "bounty platforms"}
    assert calls[0]["type"] == "function"
    assert calls[0]["id"]


def test_parse_multiple_blocks():
    content = (
        "```tool_call\n"
        '{"name": "web_search", "arguments": {"query": "a"}}\n'
        "```\n"
        "then\n"
        "```tool_call\n"
        '{"name": "execute_command", "arguments": {"command": "ls"}}\n'
        "```"
    )
    calls = parse_tool_calls_from_text(content, VALID)
    assert [c["function"]["name"] for c in calls] == ["web_search", "execute_command"]
    # IDs must be unique
    assert len({c["id"] for c in calls}) == 2


def test_parse_accepts_parameters_alias():
    # Some models emit "parameters" instead of "arguments".
    content = '```tool_call\n{"name": "web_search", "parameters": {"query": "x"}}\n```'
    calls = parse_tool_calls_from_text(content, VALID)
    assert len(calls) == 1
    assert json.loads(calls[0]["function"]["arguments"]) == {"query": "x"}


def test_parse_bare_json_object_fallback():
    # Model forgot the fence but emitted a clean JSON tool call.
    content = '{"name": "web_search", "arguments": {"query": "hello"}}'
    calls = parse_tool_calls_from_text(content, VALID)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "web_search"


def test_parse_ignores_unknown_tool_names():
    content = '```tool_call\n{"name": "not_a_real_tool", "arguments": {}}\n```'
    calls = parse_tool_calls_from_text(content, VALID)
    assert calls == []


def test_parse_ignores_plain_prose():
    content = "I would like to use web_search to find bounties, but here is my answer instead."
    calls = parse_tool_calls_from_text(content, VALID)
    assert calls == []


def test_parse_ignores_unrelated_json():
    # JSON that isn't a tool call must not be misinterpreted.
    content = '{"status": "ok", "count": 5}'
    calls = parse_tool_calls_from_text(content, VALID)
    assert calls == []


def test_parse_tolerates_trailing_text_in_block():
    content = (
        "```tool_call\n"
        '{"name": "web_search", "arguments": {"query": "x"}}\n'
        "```\n"
        "Let me know if you need anything else."
    )
    calls = parse_tool_calls_from_text(content, VALID)
    assert len(calls) == 1


def test_parse_empty_content():
    assert parse_tool_calls_from_text("", VALID) == []
    assert parse_tool_calls_from_text(None, VALID) == []


# ── flatten_tool_messages ────────────────────────────────────────────────


def test_flatten_converts_tool_calls_to_text():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": "working",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query": "x"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_0", "content": "result data"},
    ]
    out = flatten_tool_messages(messages)
    # No structured tool fields survive — prompted providers can't parse them.
    assert all("tool_calls" not in m for m in out)
    assert all(m["role"] != "tool" for m in out)
    # Semantic content is preserved as text.
    blob = "\n".join(m["content"] for m in out)
    assert "web_search" in blob
    assert "result data" in blob


def test_flatten_noop_without_tool_messages():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert flatten_tool_messages(messages) == messages
