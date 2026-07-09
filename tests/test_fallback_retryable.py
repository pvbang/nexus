"""Regression test: a retryable (transient) error must not abort the whole
fallback chain while healthy providers remain.

Symptom this guards against: "All providers failed after N attempt(s).
Available: M/total" where N is small and M is large — the chain gave up on a
retryable error (should_fallback=False) instead of trying the next provider.
"""

import pytest

from app.core.provider_fallback import (
    FallbackLLMClient,
    ProviderConfig,
    ProviderFallbackChain,
)


class _FlakyThenOkClient:
    """First provider raises a retryable error; second succeeds."""

    def __init__(self, should_fail: bool):
        self.should_fail = should_fail
        self.called = False

    async def chat(self, messages, **kwargs):
        self.called = True
        if self.should_fail:
            # "malformed_tool_call" classifies as retryable=True,
            # should_fallback=False — the exact case that used to abort.
            raise ValueError("malformed tool call in response")
        return {"content": "recovered", "role": "assistant", "tool_calls": []}


@pytest.mark.asyncio
async def test_retryable_error_falls_through_to_next_provider(monkeypatch):
    chain = ProviderFallbackChain(
        providers=[
            ProviderConfig(provider="openai", model="gpt-4o", priority=0),
            ProviderConfig(provider="openai", model="gpt-4o-mini", priority=1),
        ]
    )
    fallback = FallbackLLMClient(fallback_chain=chain)

    clients = {
        "openai:gpt-4o": _FlakyThenOkClient(should_fail=True),
        "openai:gpt-4o-mini": _FlakyThenOkClient(should_fail=False),
    }

    def fake_get_client(config, api_key_override=None):
        return clients[ProviderFallbackChain._provider_key(config)]

    monkeypatch.setattr(fallback, "_get_or_create_client", fake_get_client)
    monkeypatch.setattr(fallback, "_resolve_api_key", lambda config: None)

    result = await fallback.chat(messages=[{"role": "user", "content": "hi"}])

    # The chain advanced to the second provider instead of aborting.
    assert result["content"] == "recovered"
    assert clients["openai:gpt-4o"].called
    assert clients["openai:gpt-4o-mini"].called
