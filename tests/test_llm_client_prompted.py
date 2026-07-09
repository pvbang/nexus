"""Integration test: LLMClient.chat() prompted tool-calling end-to-end.

Verifies that for a non-native provider (e.g. g4f / pollinations), tool
schemas are injected into the prompt, native tool schemas are NOT sent to
the provider, and tool calls emitted as text are parsed back into the
native ``tool_calls`` shape the agent loop expects.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.llm_client import LLMClient


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


def _make_client(provider: str, model: str) -> LLMClient:
    """Build an LLMClient without constructing a real provider client."""
    with patch.object(
        LLMClient, "__init__", lambda self, **kw: None
    ):
        client = LLMClient()
    # Minimal attributes the chat() path touches.
    client.provider = provider
    client.model = model
    client.name = f"{provider}-test"
    from app.core.prompt_cache import PromptCacheOptimizer
    from app.services.llm_client import get_rate_limit_tracker

    client._cache_optimizer = PromptCacheOptimizer()
    client._tracker = get_rate_limit_tracker()
    return client


@pytest.mark.asyncio
async def test_prompted_provider_parses_text_tool_calls():
    client = _make_client("g4f", "gpt-4o")

    captured = {}

    async def fake_do_chat_call(messages, temperature, max_tokens, tools, tool_choice, response_format):
        # Capture what the provider actually received.
        captured["messages"] = messages
        captured["tools"] = tools
        return {
            "content": (
                "I'll search.\n"
                "```tool_call\n"
                '{"name": "web_search", "arguments": {"query": "bounties"}}\n'
                "```"
            ),
            "role": "assistant",
            "finish_reason": "stop",
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    with patch.object(client, "_do_chat_call", side_effect=fake_do_chat_call):
        result = await client.chat(
            messages=[
                {"role": "system", "content": "You are an agent."},
                {"role": "user", "content": "Find bounties."},
            ],
            tools=TOOLS,
        )

    # 1. Native tool schemas were NOT sent to the (non-native) provider.
    assert captured["tools"] is None

    # 2. Tool instructions were injected into the system prompt.
    system_msg = captured["messages"][0]
    assert system_msg["role"] == "system"
    assert "web_search" in system_msg["content"]
    assert "tool_call" in system_msg["content"]

    # 3. Text tool call was parsed back into native shape.
    assert result.get("tool_calls"), "expected tool_calls to be populated from text"
    tc = result["tool_calls"][0]
    assert tc["function"]["name"] == "web_search"
    assert json.loads(tc["function"]["arguments"]) == {"query": "bounties"}


@pytest.mark.asyncio
async def test_native_provider_unchanged():
    client = _make_client("openai", "gpt-4o")

    captured = {}

    async def fake_do_chat_call(messages, temperature, max_tokens, tools, tool_choice, response_format):
        captured["tools"] = tools
        captured["messages"] = messages
        return {
            "content": "",
            "role": "assistant",
            "finish_reason": "tool_calls",
            "tool_calls": [
                {
                    "id": "call_0",
                    "type": "function",
                    "function": {"name": "web_search", "arguments": '{"query": "x"}'},
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    with patch.object(client, "_do_chat_call", side_effect=fake_do_chat_call):
        result = await client.chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=TOOLS,
        )

    # Native provider keeps receiving native tool schemas (not stripped).
    assert captured["tools"] is not None
    # No injected tool instructions for native providers.
    assert all("HOW TO USE TOOLS" not in str(m.get("content", "")) for m in captured["messages"])
    # Native tool_calls passed through untouched.
    assert result["tool_calls"][0]["function"]["name"] == "web_search"
