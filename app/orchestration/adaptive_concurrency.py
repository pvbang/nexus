"""
Nexus - Adaptive Concurrency Limiter.

FIX GAP-7: Prevent rate limit compounding from parallel agents.

Problem: Multiple agents running in parallel hit the LLM provider's rate limit
simultaneously. All fail at once → infra_fail_streak triggers → unnecessary
backoff. The more agents, the worse the compounding effect.

Solution: Shared adaptive semaphore that:
1. Starts with max concurrency (e.g., 5 agents parallel)
2. When ANY agent gets 429/rate-limited → reduce concurrency for ALL
3. Gradually recover concurrency when requests succeed
4. Backoff is shared (one agent's 429 pauses all agents briefly)
5. Per-provider tracking (different providers have different limits)

Algorithm: AIMD (Additive Increase, Multiplicative Decrease)
- Increase: +1 slot every N successful requests
- Decrease: halve slots on any rate limit hit
- Minimum: 1 (always allow at least 1 request)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_CONCURRENCY = 5
_MIN_CONCURRENCY = 1
_RECOVERY_THRESHOLD = 10  # Successful requests before increasing concurrency
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_MAX_SECONDS = 60.0
_RATE_LIMIT_WINDOW_SECONDS = 60.0  # Track rate limits within this window


@dataclass
class ProviderState:
    """Per-provider rate limit state."""

    name: str
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY
    current_concurrency: int = _DEFAULT_MAX_CONCURRENCY
    semaphore: asyncio.Semaphore = field(default=None)
    successful_since_last_decrease: int = 0
    last_rate_limit_time: float = 0.0
    rate_limit_count_in_window: int = 0
    total_requests: int = 0
    total_rate_limits: int = 0
    backoff_until: float = 0.0  # Timestamp until which all requests should wait

    def __post_init__(self):
        if self.semaphore is None:
            self.semaphore = asyncio.Semaphore(self.current_concurrency)


class AdaptiveConcurrencyLimiter:
    """Shared concurrency control across all agents in a task.

    Uses AIMD (Additive Increase, Multiplicative Decrease):
    - Success: increment counter, add slot after N successes
    - Rate limit: halve available slots immediately, pause all briefly

    Thread-safe via asyncio primitives. Designed for single event loop.
    """

    def __init__(self, max_concurrency: int = _DEFAULT_MAX_CONCURRENCY) -> None:
        self._providers: dict[str, ProviderState] = {}
        self._default_max = max_concurrency
        self._global_backoff_until: float = 0.0
        self._lock = asyncio.Lock()

    def _get_provider(self, provider: str = "default") -> ProviderState:
        """Get or create provider state."""
        if provider not in self._providers:
            self._providers[provider] = ProviderState(
                name=provider,
                max_concurrency=self._default_max,
                current_concurrency=self._default_max,
                semaphore=asyncio.Semaphore(self._default_max),
            )
        return self._providers[provider]

    async def acquire(self, provider: str = "default") -> None:
        """Acquire a concurrency slot. Blocks if at capacity or in backoff.

        Call this before making an LLM request.
        """
        state = self._get_provider(provider)

        # Wait for global backoff (shared pain)
        now = time.monotonic()
        if self._global_backoff_until > now:
            wait = self._global_backoff_until - now
            logger.debug("concurrency_global_backoff", provider=provider, wait_s=round(wait, 1))
            await asyncio.sleep(wait)

        # Wait for provider-specific backoff
        if state.backoff_until > time.monotonic():
            wait = state.backoff_until - time.monotonic()
            logger.debug("concurrency_provider_backoff", provider=provider, wait_s=round(wait, 1))
            await asyncio.sleep(wait)

        # Acquire semaphore slot
        await state.semaphore.acquire()

    def release(self, provider: str = "default", success: bool = True) -> None:
        """Release a concurrency slot after request completes.

        Args:
            provider: Which provider was used
            success: False if the request got rate-limited (429)
        """
        state = self._get_provider(provider)
        state.semaphore.release()
        state.total_requests += 1

        if success:
            self._on_success(state)
        else:
            self._on_rate_limit(state)

    def _on_success(self, state: ProviderState) -> None:
        """Handle successful request — maybe increase concurrency."""
        state.successful_since_last_decrease += 1

        # Additive increase: restore one slot after enough successes
        if (
            state.successful_since_last_decrease >= _RECOVERY_THRESHOLD
            and state.current_concurrency < state.max_concurrency
        ):
            state.current_concurrency += 1
            state.successful_since_last_decrease = 0
            # Release an extra permit to the semaphore
            state.semaphore.release()
            logger.info(
                "concurrency_increased",
                provider=state.name,
                new_concurrency=state.current_concurrency,
            )

    def _on_rate_limit(self, state: ProviderState) -> None:
        """Handle rate limit — decrease concurrency, apply backoff."""
        state.total_rate_limits += 1
        state.successful_since_last_decrease = 0
        now = time.monotonic()

        # Track rate limits in window
        if now - state.last_rate_limit_time > _RATE_LIMIT_WINDOW_SECONDS:
            state.rate_limit_count_in_window = 0
        state.rate_limit_count_in_window += 1
        state.last_rate_limit_time = now

        # Multiplicative decrease: halve concurrency
        old_concurrency = state.current_concurrency
        state.current_concurrency = max(
            _MIN_CONCURRENCY,
            state.current_concurrency // 2,
        )

        # Drain excess semaphore permits
        permits_to_drain = old_concurrency - state.current_concurrency
        for _ in range(permits_to_drain):
            # Try to acquire without waiting (drain available permits)
            if state.semaphore._value > 0:
                state.semaphore._value -= 1

        # Calculate backoff duration (exponential based on consecutive hits)
        backoff_secs = min(
            _BACKOFF_BASE_SECONDS * (2 ** min(state.rate_limit_count_in_window - 1, 5)),
            _BACKOFF_MAX_SECONDS,
        )

        # Apply backoff to this provider
        state.backoff_until = now + backoff_secs

        # If multiple rate limits in short window, apply GLOBAL backoff
        # (protects against all providers being the same underlying service)
        if state.rate_limit_count_in_window >= 3:
            self._global_backoff_until = now + backoff_secs * 0.5

        logger.warning(
            "concurrency_decreased",
            provider=state.name,
            old=old_concurrency,
            new=state.current_concurrency,
            backoff_secs=round(backoff_secs, 1),
            rate_limits_in_window=state.rate_limit_count_in_window,
        )

    async def wait_if_needed(self, provider: str = "default") -> float:
        """Check if we need to wait (non-blocking check).

        Returns seconds until next allowed request (0 if can proceed).
        """
        state = self._get_provider(provider)
        now = time.monotonic()

        global_wait = max(0, self._global_backoff_until - now)
        provider_wait = max(0, state.backoff_until - now)

        return max(global_wait, provider_wait)

    def get_stats(self, provider: str = "default") -> dict[str, Any]:
        """Get current state for monitoring."""
        state = self._get_provider(provider)
        return {
            "provider": state.name,
            "current_concurrency": state.current_concurrency,
            "max_concurrency": state.max_concurrency,
            "available_slots": state.semaphore._value,
            "total_requests": state.total_requests,
            "total_rate_limits": state.total_rate_limits,
            "rate_limit_pct": (
                round(state.total_rate_limits / max(state.total_requests, 1) * 100, 1)
            ),
            "in_backoff": state.backoff_until > time.monotonic(),
            "successes_until_increase": max(
                0, _RECOVERY_THRESHOLD - state.successful_since_last_decrease
            ),
        }

    def get_all_stats(self) -> dict[str, Any]:
        """Get stats for all providers."""
        return {name: self.get_stats(name) for name in self._providers}


# Singleton instance (shared across all agents in a task)
_instance: AdaptiveConcurrencyLimiter | None = None


def get_concurrency_limiter(max_concurrency: int = _DEFAULT_MAX_CONCURRENCY) -> AdaptiveConcurrencyLimiter:
    """Get or create the global concurrency limiter."""
    global _instance
    if _instance is None:
        _instance = AdaptiveConcurrencyLimiter(max_concurrency)
    return _instance


def reset_concurrency_limiter() -> None:
    """Reset the global limiter (for testing or task boundaries)."""
    global _instance
    _instance = None
