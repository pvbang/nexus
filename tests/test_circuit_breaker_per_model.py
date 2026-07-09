"""Tests for H2: per-model circuit breaker isolation.

Audit §2 H2 — the circuit breaker keyed solely on `provider`, while the fallback
chain keys on `provider:model`. So when ONE bedrock model got overloaded, the
breaker tripped and rejected calls to EVERY bedrock model — collapsing healthy
capacity. The breaker must isolate per (provider, model), while still letting an
admin / token-refresh reset the whole provider family at once.
"""

from __future__ import annotations

from app.core.circuit_breaker import CircuitBreaker, CircuitState
from app.core.error_classifier import FailoverReason


def _trip(breaker, provider, model, n=5):
    for _ in range(n):
        breaker.record_failure(provider, model=model, reason=FailoverReason.server_error)


# ───────────────────────── per-model isolation ─────────────────────────


def test_tripping_one_model_does_not_open_sibling_model():
    breaker = CircuitBreaker(failure_threshold=5, failure_window=30.0)
    _trip(breaker, "bedrock", "claude-opus")
    # The overloaded model is open …
    assert breaker.can_call("bedrock", model="claude-opus") is False
    # … but its sibling on the same provider stays callable.
    assert breaker.can_call("bedrock", model="claude-haiku") is True


def test_status_keys_are_per_model():
    breaker = CircuitBreaker(failure_threshold=5)
    _trip(breaker, "bedrock", "claude-opus")
    breaker.record_success("bedrock", model="claude-haiku")
    status = breaker.get_status()
    assert "bedrock:claude-opus" in status
    assert "bedrock:claude-haiku" in status
    assert status["bedrock:claude-opus"]["state"] == CircuitState.OPEN.value
    assert status["bedrock:claude-haiku"]["state"] == CircuitState.CLOSED.value


# ───────────────────── provider-wide reset (token refresh) ─────────────────────


def test_reset_without_model_clears_all_models_of_provider():
    breaker = CircuitBreaker(failure_threshold=5)
    _trip(breaker, "bedrock", "claude-opus")
    _trip(breaker, "bedrock", "claude-sonnet")
    _trip(breaker, "openai", "gpt-4o")
    assert breaker.can_call("bedrock", model="claude-opus") is False
    assert breaker.can_call("bedrock", model="claude-sonnet") is False

    # aws_refresher resets the whole bedrock family after a token refresh.
    breaker.reset("bedrock")

    assert breaker.can_call("bedrock", model="claude-opus") is True
    assert breaker.can_call("bedrock", model="claude-sonnet") is True
    # Unrelated provider is untouched.
    assert breaker.can_call("openai", model="gpt-4o") is False


def test_reset_with_model_clears_only_that_model():
    breaker = CircuitBreaker(failure_threshold=5)
    _trip(breaker, "bedrock", "claude-opus")
    _trip(breaker, "bedrock", "claude-sonnet")
    breaker.reset("bedrock", model="claude-opus")
    assert breaker.can_call("bedrock", model="claude-opus") is True
    assert breaker.can_call("bedrock", model="claude-sonnet") is False


# ───────────────────────── backward compatibility ─────────────────────────


def test_model_omitted_keys_on_provider_only():
    # Older callsites that pass no model still work, keyed on the bare provider.
    breaker = CircuitBreaker(failure_threshold=5)
    for _ in range(5):
        breaker.record_failure("openai", reason=FailoverReason.timeout)
    assert breaker.can_call("openai") is False
    assert breaker.get_status()["openai"]["state"] == CircuitState.OPEN.value


def test_non_trippable_reason_does_not_open_model():
    breaker = CircuitBreaker(failure_threshold=3)
    for _ in range(10):
        breaker.record_failure("bedrock", model="claude-opus", reason=FailoverReason.format_error)
    assert breaker.can_call("bedrock", model="claude-opus") is True
