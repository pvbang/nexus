"""Integration test: fallback chain preserves tools for prompted providers.

Historically the fallback chain stripped ``tools`` from kwargs whenever it
landed on a free provider, which prevented LLMClient's prompted tool-calling
from ever engaging. This verifies that prompted providers now keep their
tools (so LLMClient can drive them via in-prompt instructions), while native
cross-family fallback still flattens as before.
"""

import pytest

from app.core.provider_fallback import (
    FallbackLLMClient,
    ProviderConfig,
    ProviderFallbackChain,
)


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


class _FakeClient:
    """Records the kwargs it was called with."""

    def __init__(self):
        self.received_kwargs = None

    async def chat(self, messages, **kwargs):
        self.received_kwargs = kwargs
        return {"content": "ok", "role": "assistant", "tool_calls": []}


@pytest.mark.asyncio
async def test_prompted_provider_keeps_tools_in_fallback(monkeypatch):
    chain = ProviderFallbackChain(
        providers=[ProviderConfig(provider="pollinations", model="openai", priority=0)]
    )
    fallback = FallbackLLMClient(fallback_chain=chain)

    fake = _FakeClient()
    monkeypatch.setattr(fallback, "_get_or_create_client", lambda config, api_key_override=None: fake)
    monkeypatch.setattr(fallback, "_resolve_api_key", lambda config: None)

    # History contains prior tool_calls + tool result (multi-round scenario).
    messages = [
        {"role": "user", "content": "find bounties"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c0", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c0", "content": "some result"},
    ]

    await fallback.chat(messages=messages, tools=TOOLS)

    # tools must survive — LLMClient needs them to engage prompted tool-calling.
    assert fake.received_kwargs.get("tools") == TOOLS
