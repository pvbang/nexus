"""chat() must retry on finish_reason='length' (task 3c44a93f bug #6b).

The agent react-loop calls LLMClient.chat() with max_tokens=16384. When an agent
front-loads long prose and only then emits a tool-call, the response can be cut
at max_tokens with finish_reason='length' and an UNCLOSED <invoke> block — which
the recovery parser cannot rescue (no closing tag). The result: the tool-call is
silently dropped (tools_used=0), exactly the Turn-4 narration-without-execution
failure.

chat_json() already handles this (doubles max_tokens and retries); chat() did
not. These tests pin that chat() now retries with a larger max_tokens on a
length cutoff and returns the completed response.
"""

from __future__ import annotations

import pytest

from app.services.llm_client import LLMClient


class _LengthThenComplete:
    """Stub _do_chat_call: first call truncates (length), second completes."""

    def __init__(self):
        self.calls: list[int] = []

    async def __call__(self, api_messages, temperature, max_tokens, api_tools,
                        tool_choice, response_format):
        self.calls.append(max_tokens)
        if len(self.calls) == 1:
            return {
                "content": "long prose... <invoke name=\"write_file\">",  # unclosed
                "finish_reason": "length",
                "usage": {"total_tokens": max_tokens},
            }
        return {
            "content": "done",
            "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}],
            "finish_reason": "stop",
            "usage": {"total_tokens": 10},
        }


@pytest.fixture
def _no_breaker(monkeypatch):
    """Neutralize the circuit breaker so the test exercises only retry logic."""
    import app.services.llm_client as mod

    class _OK:
        def can_call(self, *a, **k): return True
        def record_success(self, *a, **k): pass
        def record_failure(self, *a, **k): pass

    monkeypatch.setattr(mod, "get_circuit_breaker", lambda: _OK())


@pytest.mark.asyncio
async def test_chat_retries_on_length_cutoff(monkeypatch, _no_breaker):
    client = LLMClient(provider="bedrock", model="anthropic.claude-opus-4-8")
    stub = _LengthThenComplete()
    monkeypatch.setattr(client, "_do_chat_call", stub)

    result = await client.chat(
        messages=[{"role": "user", "content": "go"}],
        max_tokens=1000,
    )

    # It must have retried (2 underlying calls) with a LARGER budget the 2nd time.
    assert len(stub.calls) == 2, "chat() did not retry on finish_reason='length'"
    assert stub.calls[1] > stub.calls[0], "retry must raise max_tokens"
    # And it returns the COMPLETED response (the recovered tool call).
    assert result.get("tool_calls"), "completed response with tool_calls expected"
    assert result.get("finish_reason") == "stop"


@pytest.mark.asyncio
async def test_chat_does_not_retry_on_normal_stop(monkeypatch, _no_breaker):
    """A normal stop response is returned immediately — no extra calls."""
    client = LLMClient(provider="bedrock", model="anthropic.claude-opus-4-8")
    calls = []

    async def _one_shot(*a, **k):
        calls.append(1)
        return {"content": "ok", "finish_reason": "stop", "usage": {"total_tokens": 5}}

    monkeypatch.setattr(client, "_do_chat_call", _one_shot)

    await client.chat(messages=[{"role": "user", "content": "hi"}], max_tokens=1000)

    assert len(calls) == 1, "a normal stop must not trigger a retry"
