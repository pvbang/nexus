"""
Nexus - Credential Pool Rotation (Hermes-agent inspired).

Rotate API keys within the same provider when rate-limited.
Works alongside ProviderFallbackChain for maximum resilience.

Inspired by: hermes-agent/run_agent.py credential_pool logic

Usage:
    pool = CredentialPool(cooldown_seconds=120)
    pool.add_credentials("openrouter", ["key1", "key2", "key3"])
    pool.add_credentials("anthropic", ["sk-ant-1", "sk-ant-2"])

    key = pool.get_current_key("openrouter")   # → "key1" (or random start)
    new_key = pool.report_rate_limit("openrouter")  # → "key2"
    pool.report_success("openrouter")           # → track success for "key2"
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ── Key masking ─────────────────────────────────────────────────────────


def _mask_key(key: str) -> str:
    """Mask an API key for safe logging. Shows only last 4 chars.

    Examples:
        "sk-ant-api03-xyzABC123def" → "...3def"
        "short"                     → "...ort"
        ""                          → "...****"
    """
    if not key or len(key) < 4:
        return "...****"
    return f"...{key[-4:]}"


# ── Per-key state ───────────────────────────────────────────────────────


@dataclass
class KeyState:
    """Runtime state tracking for a single API key."""

    key_masked: str
    total_calls: int = 0
    total_successes: int = 0
    total_rate_limits: int = 0
    last_rate_limit_at: float = 0.0
    cooldown_until: float = 0.0

    @property
    def is_cooling_down(self) -> bool:
        """Check if this key is currently in cooldown."""
        if self.cooldown_until <= 0.0:
            return False
        return time.monotonic() < self.cooldown_until

    @property
    def remaining_cooldown(self) -> float:
        """Seconds remaining in cooldown, or 0 if not cooling."""
        if self.cooldown_until <= 0.0:
            return 0.0
        remaining = self.cooldown_until - time.monotonic()
        return max(0.0, remaining)


# ── Provider pool (internal) ────────────────────────────────────────────


@dataclass
class _ProviderPool:
    """Internal: pool of keys for a single provider."""

    provider: str
    keys: list[str] = field(default_factory=list)
    states: list[KeyState] = field(default_factory=list)
    current_index: int = 0


# ── Credential Pool ────────────────────────────────────────────────────


class CredentialPool:
    """Thread-safe credential pool with round-robin rotation per provider.

    Features (hermes-agent inspired):
      - Random starting position per provider (load distribution)
      - Per-key usage stats tracking
      - Cooldown period for rate-limited keys
      - Keys are NEVER logged in full — only masked ``...xxxx``
      - Thread-safe via ``threading.Lock``

    Works alongside ``ProviderFallbackChain``: credential rotation happens
    *within* a provider, while fallback chain switches *between* providers.
    """

    def __init__(self, cooldown_seconds: float = 120.0) -> None:
        self._pools: dict[str, _ProviderPool] = {}
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()

        logger.info(
            "credential_pool_initialized",
            cooldown_seconds=cooldown_seconds,
        )

    # ── Public API ──────────────────────────────────────────────────

    def add_credentials(self, provider: str, keys: list[str]) -> None:
        """Add multiple API keys for a provider.

        Args:
            provider: Provider name (e.g. ``"openrouter"``, ``"anthropic"``).
            keys: List of API key strings. Empty/whitespace keys are skipped.
        """
        valid_keys = [k.strip() for k in keys if k and k.strip()]
        if not valid_keys:
            logger.warning(
                "credential_pool_no_valid_keys",
                provider=provider,
            )
            return

        with self._lock:
            # hermes-agent: pick random starting position for load balancing
            start_index = random.randint(0, len(valid_keys) - 1)

            pool = _ProviderPool(
                provider=provider,
                keys=valid_keys,
                states=[
                    KeyState(key_masked=_mask_key(k))
                    for k in valid_keys
                ],
                current_index=start_index,
            )
            self._pools[provider] = pool

            logger.info(
                "credential_pool_keys_added",
                provider=provider,
                key_count=len(valid_keys),
                masked_keys=[_mask_key(k) for k in valid_keys],
                start_index=start_index,
            )

    def get_current_key(self, provider: str) -> Optional[str]:
        """Get the current active key for a provider.

        If the current key is in cooldown, attempts to find the next
        available key via internal rotation.

        Args:
            provider: Provider name.

        Returns:
            Current API key string, or ``None`` if provider not in pool
            or all keys are in cooldown.
        """
        with self._lock:
            pool = self._pools.get(provider)
            if pool is None or not pool.keys:
                return None

            # If current key is cooling down, try to find an available one
            if pool.states[pool.current_index].is_cooling_down:
                available_idx = self._find_available_key_index(pool)
                if available_idx is None:
                    logger.warning(
                        "credential_pool_all_keys_cooling",
                        provider=provider,
                        key_count=len(pool.keys),
                    )
                    # Return the key with shortest remaining cooldown
                    return self._get_shortest_cooldown_key(pool)
                pool.current_index = available_idx

            return pool.keys[pool.current_index]

    def rotate(self, provider: str, reason: str = "") -> Optional[str]:
        """Rotate to next available key for a provider.

        Performs round-robin rotation, skipping keys that are in cooldown.

        Args:
            provider: Provider name.
            reason: Human-readable reason for rotation (logged).

        Returns:
            New active key, or ``None`` if provider has only 1 key
            or all other keys are in cooldown.
        """
        with self._lock:
            pool = self._pools.get(provider)
            if pool is None or not pool.keys:
                return None

            if len(pool.keys) == 1:
                logger.debug(
                    "credential_pool_single_key_no_rotation",
                    provider=provider,
                )
                return None

            old_index = pool.current_index
            old_masked = pool.states[old_index].key_masked

            # Find next available key (round-robin, skip cooldowns)
            new_index = self._find_next_available(pool, start_from=old_index)
            if new_index is None or new_index == old_index:
                logger.warning(
                    "credential_pool_no_available_keys",
                    provider=provider,
                    reason=reason,
                )
                return None

            pool.current_index = new_index
            new_masked = pool.states[new_index].key_masked

            logger.info(
                "credential_pool_rotated",
                provider=provider,
                from_key=old_masked,
                to_key=new_masked,
                reason=reason,
                new_index=new_index,
            )

            return pool.keys[new_index]

    def report_rate_limit(self, provider: str) -> Optional[str]:
        """Mark current key as rate-limited, apply cooldown, auto-rotate.

        Args:
            provider: Provider name.

        Returns:
            New active key after rotation, or ``None`` if no rotation possible.
        """
        with self._lock:
            pool = self._pools.get(provider)
            if pool is None or not pool.keys:
                return None

            now = time.monotonic()
            state = pool.states[pool.current_index]
            state.total_rate_limits += 1
            state.last_rate_limit_at = now
            state.cooldown_until = now + self._cooldown_seconds

            logger.warning(
                "credential_pool_key_rate_limited",
                provider=provider,
                key=state.key_masked,
                total_rate_limits=state.total_rate_limits,
                cooldown_seconds=self._cooldown_seconds,
            )

        # rotate() acquires lock internally
        return self.rotate(provider, reason="rate_limit")

    def report_success(self, provider: str) -> None:
        """Record a successful API call for the current key.

        Args:
            provider: Provider name.
        """
        with self._lock:
            pool = self._pools.get(provider)
            if pool is None or not pool.keys:
                return

            state = pool.states[pool.current_index]
            state.total_calls += 1
            state.total_successes += 1

    def has_keys(self, provider: str) -> bool:
        """Check if a provider has keys in the pool.

        Args:
            provider: Provider name.

        Returns:
            ``True`` if provider exists and has at least one key.
        """
        with self._lock:
            pool = self._pools.get(provider)
            return pool is not None and len(pool.keys) > 0

    def get_pool_status(self) -> dict[str, Any]:
        """Return status of all providers/keys for monitoring.

        Returns:
            Dict with pool overview and per-provider/per-key stats.
            API keys are always masked.
        """
        with self._lock:
            status: dict[str, Any] = {
                "total_providers": len(self._pools),
                "cooldown_seconds": self._cooldown_seconds,
                "providers": {},
            }

            for provider, pool in self._pools.items():
                keys_info: list[dict[str, Any]] = []
                for idx, state in enumerate(pool.states):
                    keys_info.append({
                        "index": idx,
                        "key_masked": state.key_masked,
                        "is_current": idx == pool.current_index,
                        "is_cooling_down": state.is_cooling_down,
                        "remaining_cooldown": round(state.remaining_cooldown, 1),
                        "total_calls": state.total_calls,
                        "total_successes": state.total_successes,
                        "total_rate_limits": state.total_rate_limits,
                    })

                active_keys = sum(
                    1 for s in pool.states if not s.is_cooling_down
                )
                status["providers"][provider] = {
                    "total_keys": len(pool.keys),
                    "active_keys": active_keys,
                    "current_index": pool.current_index,
                    "keys": keys_info,
                }

            return status

    # ── Internal helpers (must be called within lock) ───────────────

    def _find_next_available(
        self, pool: _ProviderPool, start_from: int,
    ) -> Optional[int]:
        """Find next non-cooldown key index via round-robin.

        Starts searching from ``start_from + 1``, wrapping around.
        Returns ``None`` if no available key found (all in cooldown).
        """
        n = len(pool.keys)
        for offset in range(1, n):
            idx = (start_from + offset) % n
            if not pool.states[idx].is_cooling_down:
                return idx
        return None

    def _find_available_key_index(
        self, pool: _ProviderPool,
    ) -> Optional[int]:
        """Find any non-cooldown key index starting from current.

        Returns ``None`` if all keys are in cooldown.
        """
        n = len(pool.keys)
        for offset in range(n):
            idx = (pool.current_index + offset) % n
            if not pool.states[idx].is_cooling_down:
                return idx
        return None

    def _get_shortest_cooldown_key(self, pool: _ProviderPool) -> str:
        """Get the key with shortest remaining cooldown.

        Used as last resort when all keys are in cooldown.
        """
        min_remaining = float("inf")
        best_idx = pool.current_index

        for idx, state in enumerate(pool.states):
            remaining = state.remaining_cooldown
            if remaining < min_remaining:
                min_remaining = remaining
                best_idx = idx

        pool.current_index = best_idx
        return pool.keys[best_idx]
