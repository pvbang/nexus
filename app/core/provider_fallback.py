"""
Nexus - Provider Fallback Chain (Hermes-agent inspired).

Auto-switch to backup providers when primary fails.
Integrates with error_classifier to make smart failover decisions.

Inspired by: hermes-agent/run_agent.py fallback_models logic

Usage:
    chain = ProviderFallbackChain(providers=[
        ProviderConfig(provider="openrouter", model="anthropic/claude-sonnet-4-20250514", priority=1),
        ProviderConfig(provider="openai", model="gpt-4o", api_key="sk-...", priority=2),
        ProviderConfig(provider="gemini", model="gemini-2.0-flash", api_key="AI...", priority=3),
    ])
    fallback_client = FallbackLLMClient(fallback_chain=chain)
    result = await fallback_client.chat(messages=[...])
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

import structlog

from app.core.circuit_breaker import CircuitOpenError, get_circuit_breaker
from app.core.credential_pool import CredentialPool
from app.core.error_classifier import ClassifiedError, FailoverReason, classify_api_error
from app.core.exceptions import LLMError
from app.core.smart_wait_strategy import (
    TaskDifficulty,
    WaitDecision,
    decide_wait_or_fallback,
    estimate_reset_seconds,
    estimate_task_difficulty,
    smart_wait,
)
from app.services.llm_client import LLMClient
from app.services.prompted_tools import supports_native_tools

logger = structlog.get_logger(__name__)

# Maximum providers to try per single request (avoid infinite loops)
MAX_PROVIDERS_PER_REQUEST = 15

# Stepped cooldown ladder (OpenClaw-inspired: 30s → 1min → 5min instead of exponential)
COOLDOWN_STEPS = [30.0, 60.0, 300.0]


# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider in the fallback chain."""

    provider: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    priority: int = 0
    max_failures: int = 3
    cooldown_seconds: float = 60.0


@dataclass
class ProviderState:
    """Runtime state tracking for a provider's health."""

    failures: int = 0
    last_failure_at: float = 0.0
    is_cooled_down: bool = True
    total_calls: int = 0
    total_failures: int = 0


# ── Fallback Chain ──────────────────────────────────────────────────────


class ProviderFallbackChain:
    """Manages ordered provider list with health tracking and auto-cooldown.

    Thread-safe: all state mutations are protected by a threading.Lock.
    When a provider exceeds max_failures, it enters cooldown. After
    cooldown_seconds elapse, the provider becomes available again
    (with reset failure count) to allow recovery.
    """

    def __init__(self, providers: list[ProviderConfig]) -> None:
        if not providers:
            raise ValueError("ProviderFallbackChain requires at least one provider")

        self._providers: list[ProviderConfig] = sorted(providers, key=lambda p: p.priority)
        self._states: dict[str, ProviderState] = {
            self._provider_key(p): ProviderState() for p in self._providers
        }
        self._lock = threading.Lock()

        logger.info(
            "fallback_chain_initialized",
            providers=[self._provider_key(p) for p in self._providers],
            count=len(self._providers),
        )

    @staticmethod
    def _provider_key(config: ProviderConfig) -> str:
        """Unique key for a provider config: 'provider:model'."""
        return f"{config.provider}:{config.model}"

    def get_next_available(
        self,
        skip_provider: Optional[str] = None,
        skip_providers: Optional[set[str]] = None,
    ) -> Optional[ProviderConfig]:
        """Return the next healthy provider, optionally skipping failed ones.

        Args:
            skip_provider: Single provider key to skip (legacy, backward compat).
            skip_providers: Set of provider keys to skip (preferred).

        Returns:
            Next available ProviderConfig, or None if all are unavailable.
        """
        _skip = skip_providers or set()
        if skip_provider:
            _skip = _skip | {skip_provider}

        with self._lock:
            now = time.monotonic()
            breaker = get_circuit_breaker()
            for config in self._providers:
                key = self._provider_key(config)
                if key in _skip:
                    continue
                state = self._states[key]

                # Check if cooled down after being disabled
                if not state.is_cooled_down:
                    elapsed = now - state.last_failure_at
                    if elapsed >= config.cooldown_seconds:
                        # Cooldown expired → re-enable
                        state.is_cooled_down = True
                        state.failures = 0
                        logger.info(
                            "provider_cooldown_expired",
                            provider=key,
                            elapsed_seconds=round(elapsed, 1),
                        )
                    else:
                        continue

                # Circuit breaker: skip this (provider, model) if its circuit is open
                if not breaker.can_call(config.provider, model=config.model):
                    logger.debug(
                        "provider_skipped_circuit_open",
                        provider=key,
                    )
                    continue

                return config

        return None

    def report_success(self, provider: str) -> None:
        """Reset failure count on successful call."""
        with self._lock:
            state = self._states.get(provider)
            if state is None:
                return
            state.failures = 0
            state.is_cooled_down = True
            state.total_calls += 1

    def report_failure(self, provider: str, error: ClassifiedError) -> None:
        """Record a failure. Auto-disable provider if max_failures exceeded."""
        with self._lock:
            state = self._states.get(provider)
            if state is None:
                return

            state.failures += 1
            state.total_calls += 1
            state.total_failures += 1
            state.last_failure_at = time.monotonic()

            # Find the config to check max_failures
            config = self._find_config(provider)
            max_failures = config.max_failures if config else 3

            if state.failures >= max_failures:
                state.is_cooled_down = False
                logger.warning(
                    "provider_disabled",
                    provider=provider,
                    failures=state.failures,
                    max_failures=max_failures,
                    cooldown_seconds=config.cooldown_seconds if config else 60.0,
                    reason=error.reason.value,
                )
            else:
                logger.info(
                    "provider_failure_recorded",
                    provider=provider,
                    failures=state.failures,
                    max_failures=max_failures,
                    reason=error.reason.value,
                )

    def is_available(self, provider: str) -> bool:
        """Check if a provider is currently healthy or has cooled down."""
        with self._lock:
            state = self._states.get(provider)
            if state is None:
                return False

            if state.is_cooled_down:
                return True

            # Check cooldown expiry
            config = self._find_config(provider)
            cooldown = config.cooldown_seconds if config else 60.0
            elapsed = time.monotonic() - state.last_failure_at
            if elapsed >= cooldown:
                state.is_cooled_down = True
                state.failures = 0
                return True

            return False

    def get_chain_status(self) -> dict[str, Any]:
        """Return status of all providers for monitoring/debugging."""
        with self._lock:
            now = time.monotonic()
            status: dict[str, Any] = {
                "total_providers": len(self._providers),
                "providers": [],
            }
            for config in self._providers:
                key = self._provider_key(config)
                state = self._states[key]

                # Calculate remaining cooldown
                remaining_cooldown = 0.0
                if not state.is_cooled_down:
                    elapsed = now - state.last_failure_at
                    remaining_cooldown = max(0.0, config.cooldown_seconds - elapsed)

                status["providers"].append({
                    "key": key,
                    "provider": config.provider,
                    "model": config.model,
                    "priority": config.priority,
                    "available": state.is_cooled_down or remaining_cooldown <= 0,
                    "failures": state.failures,
                    "max_failures": config.max_failures,
                    "total_calls": state.total_calls,
                    "total_failures": state.total_failures,
                    "remaining_cooldown": round(remaining_cooldown, 1),
                })

            available_count = sum(
                1 for p in status["providers"] if p["available"]
            )
            status["available_providers"] = available_count
            return status

    def _find_config(self, provider_key: str) -> Optional[ProviderConfig]:
        """Find ProviderConfig by key. Must be called within lock."""
        for config in self._providers:
            if self._provider_key(config) == provider_key:
                return config
        return None


# ── Fallback LLM Client ────────────────────────────────────────────────


class FallbackLLMClient:
    """LLM client wrapper that automatically falls back to backup providers.

    Wraps LLMClient and uses ProviderFallbackChain + error_classifier
    to make smart failover decisions. On failure:
      1. Classify the error via classify_api_error()
      2. If should_rotate_credential → rotate key within same provider
      3. If should_fallback → try next available provider
      4. Max MAX_PROVIDERS_PER_REQUEST providers per request
      5. Log every failover for observability

    Credential pool integration:
      - Before each provider attempt, inject pool key via get_current_key()
      - On rate_limit with should_rotate_credential, rotate key first
      - On success, report_success() to pool for stats tracking
    """

    def __init__(
        self,
        fallback_chain: ProviderFallbackChain,
        credential_pool: Optional[CredentialPool] = None,
    ) -> None:
        self._chain = fallback_chain
        self._pool = credential_pool
        self._clients: dict[str, LLMClient] = {}
        # Expose primary provider's model/provider for compatibility with LLMClient interface
        primary = fallback_chain._providers[0] if fallback_chain._providers else None
        self.model = primary.model if primary else ""
        self.provider = primary.provider if primary else ""
        self.name = f"fallback-{self.provider}"

    def _get_or_create_client(
        self, config: ProviderConfig, api_key_override: Optional[str] = None,
    ) -> LLMClient:
        """Get cached LLMClient for a provider, or create one.

        Args:
            config: Provider configuration.
            api_key_override: If provided (from credential pool), use this key
                instead of config.api_key. Creates a separate cache entry.
        """
        effective_key = api_key_override or config.api_key or ""
        # Cache key includes masked api_key to support credential rotation
        cache_key = f"{ProviderFallbackChain._provider_key(config)}:{effective_key[-4:]}" if effective_key else ProviderFallbackChain._provider_key(config)

        if cache_key not in self._clients:
            self._clients[cache_key] = LLMClient(
                provider=config.provider,
                model=config.model,
                api_key=effective_key,
                base_url=config.base_url or "",
                name=f"fallback-{config.provider}",
            )
        return self._clients[cache_key]

    def _resolve_api_key(self, config: ProviderConfig) -> Optional[str]:
        """Resolve API key from credential pool if available.

        Returns pool key for the provider, or None to use config default.
        """
        if self._pool is None:
            return None
        if not self._pool.has_keys(config.provider):
            return None
        return self._pool.get_current_key(config.provider)

    def _handle_credential_rotation(
        self, config: ProviderConfig, classified: "ClassifiedError",
    ) -> bool:
        """Attempt credential rotation within the same provider.

        Returns True if a new key was found (caller should retry same provider).
        """
        if self._pool is None:
            return False
        if not classified.should_rotate_credential:
            return False
        if not self._pool.has_keys(config.provider):
            return False

        new_key = self._pool.report_rate_limit(config.provider)
        return new_key is not None

    async def _handle_bedrock_auth_refresh(
        self, config: ProviderConfig, classified: ClassifiedError,
        *, already_refreshed: bool = False,
    ) -> bool:
        """On bedrock auth failure, trigger immediate token refresh and retry.

        Returns True if refresh succeeded (caller should retry same provider).
        Only attempts once per request (already_refreshed guards against loops).
        """
        if already_refreshed:
            return False
        if config.provider != "bedrock":
            return False
        if classified.reason not in (FailoverReason.auth, FailoverReason.auth_permanent):
            return False

        try:
            from app.core.aws_refresher import get_aws_refresher, should_run_aws_refresh
            from app.config import get_settings
            # Respect the refresh toggle: if rotation is disabled (e.g. a static
            # IAM key whose auth error won't be fixed by re-fetching a session
            # token), don't attempt — let normal failover handle it.
            if not should_run_aws_refresh(get_settings()):
                return False
            refresher = get_aws_refresher()
            success = await refresher.refresh_on_auth_failure()
            if success:
                # Recreate client to pick up new env credentials
                for k in list(self._clients.keys()):
                    if k.startswith(f"{config.provider}:{config.model}"):
                        self._clients.pop(k, None)
                logger.info(
                    "bedrock_auth_refreshed_retrying",
                    provider=ProviderFallbackChain._provider_key(config),
                )
            return success
        except Exception as e:
            logger.warning("bedrock_auth_refresh_failed", error=str(e)[:200])
            return False

    async def chat(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send chat completion with automatic provider fallback.

        Tries up to MAX_PROVIDERS_PER_REQUEST providers. On each failure:
          1. If should_compress → trim messages before retry (context overflow)
          2. If should_rotate_credential → rotate key within same provider
          3. If rate_limit on preferred → smart wait (hard tasks) or fallback (easy tasks)
          4. If should_fallback → try next available provider
          5. If switching provider family → flatten tool messages to avoid format crash

        Args:
            messages: Chat messages list.
            **kwargs: Forwarded to LLMClient.chat() (temperature, max_tokens, etc.)

        Returns:
            Chat completion result dict.

        Raises:
            LLMError: If all providers fail.
        """
        skip_providers: set[str] = set()
        last_error: Optional[Exception] = None
        attempts = 0
        _overflow_compressions = 0
        _MAX_OVERFLOW_COMPRESSIONS = 2
        _bedrock_auth_refreshed = False
        _smart_wait_used = False

        # Track which family the first provider was, to detect cross-family fallback
        _original_family: Optional[str] = None
        _messages_flattened = False

        while attempts < MAX_PROVIDERS_PER_REQUEST:
            config = self._chain.get_next_available(skip_providers=skip_providers)
            if config is None:
                break

            provider_key = ProviderFallbackChain._provider_key(config)
            current_family = self._provider_family(config.provider)

            # Track the original provider family
            if _original_family is None:
                _original_family = current_family

            # When switching to a different provider family (e.g. bedrock→openai),
            # flatten tool messages to prevent format conversion crashes.
            # Prompted providers (no native function-calling, e.g. kilo/
            # pollinations/g4f) are NOT flattened here: LLMClient.chat() drives
            # them via prompted tool-calling (in-prompt instructions + text
            # parsing), so tools must stay in kwargs for that path to engage.
            _is_prompted_provider = not supports_native_tools(
                config.provider, config.model
            )
            _needs_flatten = (
                not _messages_flattened
                and not _is_prompted_provider
                and current_family != _original_family
                and any(m.get("tool_calls") or m.get("role") == "tool" for m in messages)
            )
            if _needs_flatten:
                messages = self._flatten_tool_messages(messages)
                _messages_flattened = True
                # Also strip tools from kwargs — flattened context can't use tools
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                logger.info(
                    "fallback_tool_messages_flattened",
                    target_provider=provider_key,
                    reason="cross-family native fallback",
                )

            pool_key = self._resolve_api_key(config)
            client = self._get_or_create_client(config, api_key_override=pool_key)
            attempts += 1

            try:
                result = await client.chat(messages=messages, **kwargs)
                self._chain.report_success(provider_key)
                if self._pool is not None:
                    self._pool.report_success(config.provider)
                return result

            except CircuitOpenError:
                # Circuit breaker already open for this provider — skip immediately
                skip_providers.add(provider_key)
                logger.info(
                    "fallback_skip_circuit_open",
                    provider=provider_key,
                )
                continue

            except Exception as e:
                last_error = e
                classified = classify_api_error(
                    e,
                    provider=config.provider,
                    model=config.model,
                    num_messages=len(messages),
                )
                self._chain.report_failure(provider_key, classified)

                # Tool format errors: flatten and retry same or next provider
                _is_tool_format_error = (
                    "tool call" in str(e).lower()
                    or "tool_call" in str(e).lower()
                    or "_convert_to_bedrock_tool" in str(e)
                )
                if _is_tool_format_error and not _messages_flattened:
                    messages = self._flatten_tool_messages(messages)
                    _messages_flattened = True
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    logger.warning(
                        "fallback_tool_format_error_flattened",
                        provider=provider_key,
                        error=str(e)[:200],
                    )
                    # Retry same provider with flattened messages
                    attempts -= 1
                    continue

                if classified.should_compress and _overflow_compressions < _MAX_OVERFLOW_COMPRESSIONS:
                    _overflow_compressions += 1
                    _original_count = len(messages)
                    messages = self._emergency_trim_messages(messages)
                    logger.info(
                        "fallback_context_overflow_trimmed",
                        provider=provider_key,
                        original_messages=_original_count,
                        trimmed_messages=len(messages),
                        compression_attempt=_overflow_compressions,
                    )
                    # DON'T skip this provider — retry same one with smaller context
                    attempts -= 1
                    continue

                # Bedrock auth failure: refresh token immediately and retry (once)
                if await self._handle_bedrock_auth_refresh(
                    config, classified, already_refreshed=_bedrock_auth_refreshed,
                ):
                    _bedrock_auth_refreshed = True
                    attempts -= 1
                    continue

                # Try credential rotation before provider fallback
                if self._handle_credential_rotation(config, classified):
                    logger.info(
                        "credential_rotated_retrying",
                        provider=provider_key,
                        reason=classified.reason.value,
                        attempt=attempts,
                    )
                    # Don't increment attempts — retry same provider with new key
                    attempts -= 1
                    continue

                # ── Smart Wait: rate limit on preferred provider ──
                # Instead of immediately falling back to weaker models,
                # evaluate task difficulty and potentially wait for reset.
                if (
                    classified.reason == FailoverReason.rate_limit
                    and not _smart_wait_used
                    and self._should_smart_wait(config)
                ):
                    verdict = await self._try_smart_wait(
                        config, provider_key, messages, kwargs.get("tools"),
                    )
                    _smart_wait_used = True
                    if verdict and verdict.decision == WaitDecision.WAIT:
                        # Waited successfully — retry same provider
                        attempts -= 1
                        continue

                # Always add failed provider to skip set to prevent ping-pong
                skip_providers.add(provider_key)

                # Fall back to the next provider when the error is either
                # explicitly fallback-able OR merely retryable (transient).
                # A retryable error that already had its in-place recovery
                # (compress / rotate / smart-wait) exhausted above should try
                # a different provider instead of aborting the whole chain —
                # otherwise a single transient error stops a chain that still
                # has many healthy providers ("failed after N / Available M").
                if (
                    (classified.should_fallback or classified.retryable)
                    and attempts < MAX_PROVIDERS_PER_REQUEST
                ):
                    next_config = self._chain.get_next_available(
                        skip_providers=skip_providers,
                    )
                    if next_config is not None:
                        next_key = ProviderFallbackChain._provider_key(next_config)
                        logger.warning(
                            "provider_failover",
                            from_provider=provider_key,
                            to_provider=next_key,
                            reason=classified.reason.value,
                            retryable=classified.retryable,
                            attempt=attempts,
                        )
                        continue

                # Not fallback-able or no more providers
                break

        chain_status = self._chain.get_chain_status()
        raise LLMError(
            f"All providers failed after {attempts} attempt(s). "
            f"Available: {chain_status['available_providers']}/{chain_status['total_providers']}. "
            f"Last error: {last_error}"
        )

    @staticmethod
    def _provider_family(provider: str) -> str:
        """Classify provider into format family for tool-call compatibility."""
        p = (provider or "").lower()
        if p == "bedrock":
            return "bedrock"
        # All others use OpenAI-compatible format
        return "openai"

    @staticmethod
    def _flatten_tool_messages(messages: list[dict]) -> list[dict]:
        """Convert tool_call/tool_result messages into plain text equivalents.

        When falling back to a different provider family (bedrock→openai or vice versa),
        tool_call_id formats are incompatible and cause litellm conversion crashes.
        Also, many free models (Kilo, Pollinations, g4f) don't support tools at all.

        This converts:
          - assistant message with tool_calls → assistant message with text summary
          - tool role messages → user message with tool result text

        Preserves the semantic content while removing format-dependent structures.
        """
        if not messages:
            return messages

        # Check if there are any tool messages to flatten
        has_tools = any(
            msg.get("tool_calls") or msg.get("role") == "tool"
            for msg in messages
        )
        if not has_tools:
            return messages

        result: list[dict] = []
        pending_tool_results: list[dict] = []

        for msg in messages:
            role = msg.get("role", "")

            if role == "assistant" and msg.get("tool_calls"):
                # Flatten: assistant tool_call → plain assistant text
                content_parts = []
                if msg.get("content"):
                    content_parts.append(msg["content"])
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    name = fn.get("name", "unknown_tool")
                    args = fn.get("arguments", "")
                    # Truncate long args
                    if len(args) > 500:
                        args = args[:500] + "..."
                    content_parts.append(f"[Called tool: {name}({args})]")
                result.append({
                    "role": "assistant",
                    "content": "\n".join(content_parts),
                })

            elif role == "tool":
                # Collect tool results to merge into a user message
                tool_content = msg.get("content", "") or ""
                if len(tool_content) > 2000:
                    tool_content = tool_content[:2000] + "...[truncated]"
                pending_tool_results.append(
                    f"[Tool result: {tool_content}]"
                )

            else:
                # Flush any pending tool results before this message
                if pending_tool_results:
                    result.append({
                        "role": "user",
                        "content": "\n".join(pending_tool_results),
                    })
                    pending_tool_results = []
                result.append(msg)

        # Flush remaining tool results
        if pending_tool_results:
            result.append({
                "role": "user",
                "content": "\n".join(pending_tool_results),
            })

        return result

    @staticmethod
    def _emergency_trim_messages(messages: list[dict]) -> list[dict]:
        """Emergency context trim for fallback overflow recovery.

        Keeps system prompt + last 40% of messages + truncates long content.
        Non-LLM-powered, fast, suitable for use in retry loops.
        """
        if not messages or len(messages) <= 4:
            return messages

        system_msg = messages[0]
        rest = messages[1:]

        # Keep last 40% (minimum 4 messages)
        keep_count = max(4, len(rest) * 40 // 100)
        kept = rest[-keep_count:]

        # Truncate long content in kept messages
        result = [system_msg]
        for msg in kept:
            content = msg.get("content", "") or ""
            if len(content) > 6000:
                truncated = content[:3000] + "\n...[trimmed]...\n" + content[-1500:]
                result.append({**msg, "content": truncated})
            else:
                result.append(msg)

        return result

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream chat completion with automatic provider fallback.

        Same fallback + credential rotation logic as chat(), but for streaming.
        On failure mid-stream, falls back to next provider and restarts.
        Includes tool message flattening for cross-family fallback.

        Args:
            messages: Chat messages list.
            **kwargs: Forwarded to LLMClient.chat_stream().

        Yields:
            Content chunks as strings.

        Raises:
            LLMError: If all providers fail.
        """
        skip_providers: set[str] = set()
        last_error: Optional[Exception] = None
        attempts = 0
        _original_family: Optional[str] = None
        _messages_flattened = False
        _bedrock_auth_refreshed = False
        _smart_wait_used = False

        while attempts < MAX_PROVIDERS_PER_REQUEST:
            config = self._chain.get_next_available(skip_providers=skip_providers)
            if config is None:
                break

            provider_key = ProviderFallbackChain._provider_key(config)
            current_family = self._provider_family(config.provider)

            if _original_family is None:
                _original_family = current_family

            # Flatten tool messages when switching provider family or hitting free providers
            _is_free_provider = config.provider in ("kilo", "pollinations", "g4f")
            _needs_flatten = (
                not _messages_flattened
                and (current_family != _original_family or _is_free_provider)
                and any(m.get("tool_calls") or m.get("role") == "tool" for m in messages)
            )
            if _needs_flatten:
                messages = self._flatten_tool_messages(messages)
                _messages_flattened = True
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)

            pool_key = self._resolve_api_key(config)
            client = self._get_or_create_client(config, api_key_override=pool_key)
            attempts += 1

            try:
                breaker = get_circuit_breaker()
                async for chunk in client.chat_stream(messages=messages, **kwargs):
                    yield chunk
                # Record success in both chain and circuit breaker
                self._chain.report_success(provider_key)
                breaker.record_success(config.provider, model=config.model)
                if self._pool is not None:
                    self._pool.report_success(config.provider)
                return

            except CircuitOpenError:
                # Circuit breaker already open for this provider — skip immediately
                skip_providers.add(provider_key)
                logger.info(
                    "fallback_skip_circuit_open",
                    provider=provider_key,
                    mode="stream",
                )
                continue

            except Exception as e:
                last_error = e
                classified = classify_api_error(
                    e,
                    provider=config.provider,
                    model=config.model,
                    num_messages=len(messages),
                )
                self._chain.report_failure(provider_key, classified)
                # Record failure in circuit breaker (only trips for infra errors)
                breaker = get_circuit_breaker()
                breaker.record_failure(config.provider, e, classified.reason, model=config.model)

                # Tool format errors: flatten and retry
                _is_tool_format_error = (
                    "tool call" in str(e).lower()
                    or "tool_call" in str(e).lower()
                    or "_convert_to_bedrock_tool" in str(e)
                )
                if _is_tool_format_error and not _messages_flattened:
                    messages = self._flatten_tool_messages(messages)
                    _messages_flattened = True
                    kwargs.pop("tools", None)
                    kwargs.pop("tool_choice", None)
                    attempts -= 1
                    continue

                # Bedrock auth failure: refresh token immediately and retry (once)
                if await self._handle_bedrock_auth_refresh(
                    config, classified, already_refreshed=_bedrock_auth_refreshed,
                ):
                    _bedrock_auth_refreshed = True
                    attempts -= 1
                    continue

                # Try credential rotation before provider fallback
                if self._handle_credential_rotation(config, classified):
                    logger.info(
                        "credential_rotated_retrying",
                        provider=provider_key,
                        reason=classified.reason.value,
                        attempt=attempts,
                        mode="stream",
                    )
                    attempts -= 1
                    continue

                # ── Smart Wait: rate limit on preferred provider ──
                if (
                    classified.reason == FailoverReason.rate_limit
                    and not _smart_wait_used
                    and self._should_smart_wait(config)
                ):
                    verdict = await self._try_smart_wait(
                        config, provider_key, messages, kwargs.get("tools"),
                    )
                    _smart_wait_used = True
                    if verdict and verdict.decision == WaitDecision.WAIT:
                        attempts -= 1
                        continue

                # Always add failed provider to skip set to prevent ping-pong
                skip_providers.add(provider_key)

                if classified.should_fallback and attempts < MAX_PROVIDERS_PER_REQUEST:
                    next_config = self._chain.get_next_available(
                        skip_providers=skip_providers,
                    )
                    if next_config is not None:
                        next_key = ProviderFallbackChain._provider_key(next_config)
                        logger.warning(
                            "provider_failover",
                            from_provider=provider_key,
                            to_provider=next_key,
                            reason=classified.reason.value,
                            attempt=attempts,
                            mode="stream",
                        )
                        continue

                break

        chain_status = self._chain.get_chain_status()
        raise LLMError(
            f"All providers failed streaming after {attempts} attempt(s). "
            f"Available: {chain_status['available_providers']}/{chain_status['total_providers']}. "
            f"Last error: {last_error}"
        )

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Chat and parse JSON response with automatic provider fallback.

        Same fallback + credential rotation logic as chat(), but uses
        LLMClient.chat_json() for JSON-mode responses.

        Args:
            messages: Chat messages list.
            **kwargs: Forwarded to LLMClient.chat_json().

        Returns:
            Parsed JSON dict.

        Raises:
            LLMError: If all providers fail.
        """
        skip_providers: set[str] = set()
        last_error: Optional[Exception] = None
        attempts = 0
        _bedrock_auth_refreshed = False
        _smart_wait_used = False

        while attempts < MAX_PROVIDERS_PER_REQUEST:
            config = self._chain.get_next_available(skip_providers=skip_providers)
            if config is None:
                break

            provider_key = ProviderFallbackChain._provider_key(config)
            pool_key = self._resolve_api_key(config)
            client = self._get_or_create_client(config, api_key_override=pool_key)
            attempts += 1

            try:
                result = await client.chat_json(messages=messages, **kwargs)
                self._chain.report_success(provider_key)
                if self._pool is not None:
                    self._pool.report_success(config.provider)
                return result

            except CircuitOpenError:
                # Circuit breaker already open for this provider — skip immediately
                skip_providers.add(provider_key)
                logger.info(
                    "fallback_skip_circuit_open",
                    provider=provider_key,
                    mode="json",
                )
                continue

            except Exception as e:
                last_error = e
                classified = classify_api_error(
                    e,
                    provider=config.provider,
                    model=config.model,
                    num_messages=len(messages),
                )
                self._chain.report_failure(provider_key, classified)

                # Bedrock auth failure: refresh token immediately and retry (once)
                if await self._handle_bedrock_auth_refresh(
                    config, classified, already_refreshed=_bedrock_auth_refreshed,
                ):
                    _bedrock_auth_refreshed = True
                    attempts -= 1
                    continue

                # Try credential rotation before provider fallback
                if self._handle_credential_rotation(config, classified):
                    logger.info(
                        "credential_rotated_retrying",
                        provider=provider_key,
                        reason=classified.reason.value,
                        attempt=attempts,
                        mode="json",
                    )
                    attempts -= 1
                    continue

                # ── Smart Wait: rate limit on preferred provider ──
                if (
                    classified.reason == FailoverReason.rate_limit
                    and not _smart_wait_used
                    and self._should_smart_wait(config)
                ):
                    verdict = await self._try_smart_wait(
                        config, provider_key, messages, None,
                    )
                    _smart_wait_used = True
                    if verdict and verdict.decision == WaitDecision.WAIT:
                        attempts -= 1
                        continue

                # Always add failed provider to skip set to prevent ping-pong
                skip_providers.add(provider_key)

                if classified.should_fallback and attempts < MAX_PROVIDERS_PER_REQUEST:
                    next_config = self._chain.get_next_available(
                        skip_providers=skip_providers,
                    )
                    if next_config is not None:
                        next_key = ProviderFallbackChain._provider_key(next_config)
                        logger.warning(
                            "provider_failover",
                            from_provider=provider_key,
                            to_provider=next_key,
                            reason=classified.reason.value,
                            attempt=attempts,
                            mode="json",
                        )
                        continue

                break

        chain_status = self._chain.get_chain_status()
        raise LLMError(
            f"All providers failed JSON chat after {attempts} attempt(s). "
            f"Available: {chain_status['available_providers']}/{chain_status['total_providers']}. "
            f"Last error: {last_error}"
        )

    async def chat_streaming_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 16384,
        on_token: Optional[Any] = None,
    ) -> dict[str, Any]:
        """Stream chat completion with tools and automatic provider fallback.

        Same fallback + credential rotation logic as chat(), but uses
        LLMClient.chat_streaming_with_tools() for streaming tool-call support.

        Args:
            messages: Chat messages list.
            tools: Tool schemas for LLM.
            temperature: LLM temperature.
            max_tokens: Max tokens to generate.
            on_token: Optional async callback for each content token.

        Returns:
            Chat completion result dict (same format as chat()).

        Raises:
            LLMError: If all providers fail.
        """
        skip_providers: set[str] = set()
        last_error: Optional[Exception] = None
        attempts = 0
        _bedrock_auth_refreshed = False
        _smart_wait_used = False

        while attempts < MAX_PROVIDERS_PER_REQUEST:
            config = self._chain.get_next_available(skip_providers=skip_providers)
            if config is None:
                break

            provider_key = ProviderFallbackChain._provider_key(config)
            pool_key = self._resolve_api_key(config)
            client = self._get_or_create_client(config, api_key_override=pool_key)
            attempts += 1

            try:
                result = await client.chat_streaming_with_tools(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    on_token=on_token,
                )
                # Record success in circuit breaker (streaming_with_tools bypasses LLMClient.chat())
                breaker = get_circuit_breaker()
                breaker.record_success(config.provider, model=config.model)
                self._chain.report_success(provider_key)
                if self._pool is not None:
                    self._pool.report_success(config.provider)
                return result

            except CircuitOpenError:
                # Circuit breaker already open for this provider — skip immediately
                skip_providers.add(provider_key)
                logger.info(
                    "fallback_skip_circuit_open",
                    provider=provider_key,
                    mode="streaming_with_tools",
                )
                continue

            except Exception as e:
                last_error = e
                classified = classify_api_error(
                    e,
                    provider=config.provider,
                    model=config.model,
                    num_messages=len(messages),
                )
                self._chain.report_failure(provider_key, classified)
                # Record failure in circuit breaker (only trips for infra errors)
                breaker = get_circuit_breaker()
                breaker.record_failure(config.provider, e, classified.reason, model=config.model)

                # Bedrock auth failure: refresh token immediately and retry (once)
                if await self._handle_bedrock_auth_refresh(
                    config, classified, already_refreshed=_bedrock_auth_refreshed,
                ):
                    _bedrock_auth_refreshed = True
                    attempts -= 1
                    continue

                # Try credential rotation before provider fallback
                if self._handle_credential_rotation(config, classified):
                    logger.info(
                        "credential_rotated_retrying",
                        provider=provider_key,
                        reason=classified.reason.value,
                        attempt=attempts,
                        mode="streaming_with_tools",
                    )
                    attempts -= 1
                    continue

                # ── Smart Wait: rate limit on preferred provider ──
                if (
                    classified.reason == FailoverReason.rate_limit
                    and not _smart_wait_used
                    and self._should_smart_wait(config)
                ):
                    verdict = await self._try_smart_wait(
                        config, provider_key, messages, tools,
                    )
                    _smart_wait_used = True
                    if verdict and verdict.decision == WaitDecision.WAIT:
                        attempts -= 1
                        continue

                # Always add failed provider to skip set to prevent ping-pong
                skip_providers.add(provider_key)

                if classified.should_fallback and attempts < MAX_PROVIDERS_PER_REQUEST:
                    next_config = self._chain.get_next_available(
                        skip_providers=skip_providers,
                    )
                    if next_config is not None:
                        next_key = ProviderFallbackChain._provider_key(next_config)
                        logger.warning(
                            "provider_failover",
                            from_provider=provider_key,
                            to_provider=next_key,
                            reason=classified.reason.value,
                            attempt=attempts,
                            mode="streaming_with_tools",
                        )
                        continue

                break

        chain_status = self._chain.get_chain_status()
        raise LLMError(
            f"All providers failed streaming_with_tools after {attempts} attempt(s). "
            f"Available: {chain_status['available_providers']}/{chain_status['total_providers']}. "
            f"Last error: {last_error}"
        )

    def _should_smart_wait(self, config: ProviderConfig) -> bool:
        """Check if this provider is worth waiting for (preferred/high-quality)."""
        from app.config import get_settings
        s = get_settings()
        if not s.smart_wait_enabled:
            return False
        # Only wait for providers in the top priority tier (priority 0 or 1)
        # or known high-quality free providers
        _preferred_providers = {"kilo", "bedrock", "openrouter"}
        return config.provider in _preferred_providers or config.priority <= 1

    async def _try_smart_wait(
        self,
        config: ProviderConfig,
        provider_key: str,
        messages: list[dict],
        tools: Optional[list] = None,
    ) -> Optional["WaitVerdict"]:
        """Evaluate task difficulty and decide whether to wait for rate limit reset."""
        from app.config import get_settings
        from app.core.smart_wait_strategy import WaitVerdict
        s = get_settings()

        difficulty = estimate_task_difficulty(messages, tools=tools)

        # Get rate limit state from tracker for better reset estimation
        from app.services.llm_client import get_rate_limit_tracker
        tracker = get_rate_limit_tracker()
        state = tracker.get_state(config.provider)

        retry_after = None
        reset_seconds = None
        if state and state.has_data:
            # Use the most constrained bucket's reset time
            for bucket in (state.requests_min, state.requests_hour):
                if bucket.limit > 0 and bucket.remaining <= 0:
                    reset_seconds = bucket.remaining_seconds_now
                    break

        wait_estimate = estimate_reset_seconds(
            retry_after_header=retry_after,
            rate_limit_remaining=state.requests_hour.remaining if state else None,
            rate_limit_reset_seconds=reset_seconds,
            last_rate_limit_at=time.time(),
            provider=config.provider,
        )

        verdict = decide_wait_or_fallback(
            difficulty,
            wait_estimate,
            max_wait_seconds=float(s.smart_wait_max_seconds),
            provider_key=provider_key,
        )

        logger.info(
            "smart_wait_decision",
            provider=provider_key,
            difficulty=difficulty.value,
            estimated_reset_s=round(wait_estimate, 1),
            decision=verdict.decision.value,
            wait_seconds=round(verdict.wait_seconds, 1),
            reason=verdict.reason,
        )

        if verdict.decision == WaitDecision.WAIT:
            waited = await smart_wait(
                verdict.wait_seconds,
                provider_key=provider_key,
                check_interval=float(s.smart_wait_check_interval),
                is_available_fn=lambda: self._chain.is_available(provider_key),
            )
            if waited:
                # Reset the provider's failure state so it can be retried
                self._chain.report_success(provider_key)
            return verdict

        return verdict

    def get_chain_status(self) -> dict[str, Any]:
        """Return combined status of fallback chain and credential pool."""
        status = self._chain.get_chain_status()
        if self._pool is not None:
            status["credential_pool"] = self._pool.get_pool_status()
        return status
