"""
Nexus - Rate Limit Tracker (Hermes-agent inspired).

Captures rate limit headers from provider responses and provides
formatted display for monitoring endpoints.

Inspired by: hermes-agent/agent/rate_limit_tracker.py
"""

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class RateLimitBucket:
    """One rate-limit window (e.g. requests per minute)."""
    limit: int = 0
    remaining: int = 0
    reset_seconds: float = 0.0
    captured_at: float = 0.0

    @property
    def used(self) -> int:
        return max(0, self.limit - self.remaining)

    @property
    def usage_pct(self) -> float:
        if self.limit <= 0:
            return 0.0
        return (self.used / self.limit) * 100.0

    @property
    def remaining_seconds_now(self) -> float:
        elapsed = time.time() - self.captured_at
        return max(0.0, self.reset_seconds - elapsed)


@dataclass
class RateLimitState:
    """Full rate-limit state parsed from response headers."""
    requests_min: RateLimitBucket = field(default_factory=RateLimitBucket)
    requests_hour: RateLimitBucket = field(default_factory=RateLimitBucket)
    tokens_min: RateLimitBucket = field(default_factory=RateLimitBucket)
    tokens_hour: RateLimitBucket = field(default_factory=RateLimitBucket)
    captured_at: float = 0.0
    provider: str = ""

    @property
    def has_data(self) -> bool:
        return self.captured_at > 0

    @property
    def age_seconds(self) -> float:
        if not self.has_data:
            return float("inf")
        return time.time() - self.captured_at


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_rate_limit_headers(
    headers: Mapping[str, str],
    provider: str = "",
) -> Optional[RateLimitState]:
    """Parse x-ratelimit-* headers into a RateLimitState."""
    lowered = {k.lower(): v for k, v in headers.items()}
    has_any = any(k.startswith("x-ratelimit-") for k in lowered)
    if not has_any:
        return None

    now = time.time()

    def _bucket(resource: str, suffix: str = "") -> RateLimitBucket:
        tag = f"{resource}{suffix}"
        return RateLimitBucket(
            limit=_safe_int(lowered.get(f"x-ratelimit-limit-{tag}")),
            remaining=_safe_int(lowered.get(f"x-ratelimit-remaining-{tag}")),
            reset_seconds=_safe_float(lowered.get(f"x-ratelimit-reset-{tag}")),
            captured_at=now,
        )

    return RateLimitState(
        requests_min=_bucket("requests"),
        requests_hour=_bucket("requests", "-1h"),
        tokens_min=_bucket("tokens"),
        tokens_hour=_bucket("tokens", "-1h"),
        captured_at=now,
        provider=provider,
    )


def _fmt_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_seconds(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s"
    h, remainder = divmod(s, 3600)
    m = remainder // 60
    return f"{h}h {m}m"


def format_rate_limit_display(state: RateLimitState) -> dict[str, Any]:
    """Format rate limit state for API response."""
    if not state.has_data:
        return {"status": "no_data", "message": "No rate limit data yet"}

    buckets = {}
    for label, bucket in [
        ("requests_min", state.requests_min),
        ("requests_hour", state.requests_hour),
        ("tokens_min", state.tokens_min),
        ("tokens_hour", state.tokens_hour),
    ]:
        if bucket.limit > 0:
            buckets[label] = {
                "limit": bucket.limit,
                "used": bucket.used,
                "remaining": bucket.remaining,
                "usage_pct": round(bucket.usage_pct, 1),
                "reset_in": _fmt_seconds(bucket.remaining_seconds_now),
            }

    warnings = []
    for label, bucket in [
        ("requests/min", state.requests_min),
        ("tokens/min", state.tokens_min),
    ]:
        if bucket.limit > 0 and bucket.usage_pct >= 80:
            warnings.append(f"⚠ {label} at {bucket.usage_pct:.0f}%")

    return {
        "status": "ok",
        "provider": state.provider,
        "age_seconds": round(state.age_seconds, 1),
        "buckets": buckets,
        "warnings": warnings,
    }


class RateLimitTracker:
    """Track rate limits across multiple providers."""

    def __init__(self) -> None:
        self._states: dict[str, RateLimitState] = {}

    def update(self, provider: str, headers: Mapping[str, str]) -> None:
        """Update rate limit state from response headers."""
        state = parse_rate_limit_headers(headers, provider)
        if state:
            self._states[provider] = state

    def get_state(self, provider: str) -> Optional[RateLimitState]:
        return self._states.get(provider)

    def get_all_states(self) -> dict[str, RateLimitState]:
        return dict(self._states)

    def record_rate_limit_hit(self, provider: str, retry_after: Optional[float] = None) -> None:
        """Record that a rate limit was hit, with optional Retry-After value."""
        now = time.time()
        state = self._states.get(provider)
        if state is None:
            state = RateLimitState(provider=provider, captured_at=now)
            self._states[provider] = state
        state.captured_at = now
        # Store retry_after in the most relevant bucket
        if retry_after and retry_after > 0:
            state.requests_hour = RateLimitBucket(
                limit=state.requests_hour.limit or 200,
                remaining=0,
                reset_seconds=retry_after,
                captured_at=now,
            )

    def get_retry_after(self, provider: str) -> Optional[float]:
        """Get remaining seconds from last known rate limit for this provider."""
        state = self._states.get(provider)
        if not state or not state.has_data:
            return None
        for bucket in (state.requests_min, state.requests_hour):
            if bucket.limit > 0 and bucket.remaining <= 0:
                remaining = bucket.remaining_seconds_now
                if remaining > 0:
                    return remaining
        return None

    def is_near_limit(self, provider: str, threshold_pct: float = 80.0) -> bool:
        """Check if any bucket is near the rate limit."""
        state = self._states.get(provider)
        if not state or not state.has_data:
            return False
        for bucket in [state.requests_min, state.tokens_min]:
            if bucket.limit > 0 and bucket.usage_pct >= threshold_pct:
                return True
        return False
