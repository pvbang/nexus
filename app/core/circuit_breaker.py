"""
Circuit Breaker Pattern cho LLM provider calls.

States:
- CLOSED: Normal operation, track failures
- OPEN: Provider unavailable, reject immediately (no wasted $ on retries)
- HALF_OPEN: Probe with single call to test recovery

Behavior:
- Track failures per provider (anthropic, openai, google, etc.)
- Trip threshold: N consecutive failures within window → OPEN
- Recovery: After timeout in OPEN → transition to HALF_OPEN → 1 probe call
- If probe succeeds → CLOSED (reset)
- If probe fails → back to OPEN (restart timer)

Error classification:
- Only trips for infrastructure errors (timeout, 500, 502, 503, 429, auth)
- Does NOT trip for logic errors (400, parse errors, content policy, context overflow)
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional

import structlog

from app.core.error_classifier import FailoverReason

logger = structlog.get_logger(__name__)

# Errors that should trip the circuit breaker (infrastructure failures)
_TRIPPABLE_REASONS: frozenset[FailoverReason] = frozenset({
    FailoverReason.timeout,
    FailoverReason.server_error,
    FailoverReason.overloaded,
    FailoverReason.rate_limit,
    FailoverReason.auth,
})


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ProviderCircuit:
    """State for a single provider's circuit."""

    provider: str
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    failure_timestamps: list = field(default_factory=list)
    last_failure_time: float = 0.0
    opened_at: float = 0.0
    # Configurable
    failure_threshold: int = 5
    failure_window_seconds: float = 30.0
    recovery_timeout_seconds: float = 60.0


class CircuitBreaker:
    """
    Manages circuit breakers for multiple LLM providers, isolated per model.

    Each (provider, model) pair gets its own circuit (audit H2): one overloaded
    model must not trip the breaker for every other model on the same provider.
    Callers that omit `model` key on the bare provider (backward compatible).

    Usage:
        breaker = get_circuit_breaker()

        if not breaker.can_call("bedrock", model="claude-opus"):
            raise CircuitOpenError("bedrock", model="claude-opus")

        try:
            result = await llm_call(...)
            breaker.record_success("bedrock", model="claude-opus")
        except Exception as e:
            breaker.record_failure("bedrock", e, model="claude-opus")
            raise
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        failure_window: float = 30.0,
        recovery_timeout: float = 60.0,
    ):
        self._circuits: dict[str, ProviderCircuit] = {}
        self._failure_threshold = failure_threshold
        self._failure_window = failure_window
        self._recovery_timeout = recovery_timeout
        self._metrics: dict[str, dict] = {}  # Per-circuit health metrics

    @staticmethod
    def _circuit_key(provider: str, model: Optional[str]) -> str:
        """Key a circuit per (provider, model); bare provider when no model."""
        return f"{provider}:{model}" if model else provider

    def _get_circuit(self, provider: str, model: Optional[str] = None) -> ProviderCircuit:
        """Get or create circuit for a (provider, model) pair."""
        key = self._circuit_key(provider, model)
        if key not in self._circuits:
            self._circuits[key] = ProviderCircuit(
                provider=key,
                failure_threshold=self._failure_threshold,
                failure_window_seconds=self._failure_window,
                recovery_timeout_seconds=self._recovery_timeout,
            )
        return self._circuits[key]

    def can_call(self, provider: str, model: Optional[str] = None) -> bool:
        """Check if this (provider, model) is available (not tripped)."""
        circuit = self._get_circuit(provider, model)
        now = time.monotonic()

        if circuit.state == CircuitState.CLOSED:
            return True
        elif circuit.state == CircuitState.OPEN:
            # Check if recovery timeout elapsed
            if now - circuit.opened_at >= circuit.recovery_timeout_seconds:
                circuit.state = CircuitState.HALF_OPEN
                logger.info(
                    "circuit_breaker_half_open",
                    provider=provider,
                    msg="OPEN → HALF_OPEN (probing)",
                )
                return True  # Allow one probe call
            return False
        elif circuit.state == CircuitState.HALF_OPEN:
            # Allow probe call through
            return True
        return False

    def record_success(self, provider: str, latency_ms: float = 0, model: Optional[str] = None) -> None:
        """Record successful call. Reset circuit if in HALF_OPEN. Track metrics."""
        key = self._circuit_key(provider, model)
        circuit = self._get_circuit(provider, model)
        if circuit.state == CircuitState.HALF_OPEN:
            logger.info(
                "circuit_breaker_closed",
                provider=key,
                msg="HALF_OPEN → CLOSED (probe succeeded)",
            )
            circuit.state = CircuitState.CLOSED
            circuit.failure_count = 0
            circuit.failure_timestamps.clear()
        elif circuit.state == CircuitState.CLOSED:
            circuit.failure_count = 0
            circuit.failure_timestamps.clear()

        # Track per-circuit health metrics
        metrics = self._metrics.setdefault(key, {
            "total_calls": 0, "successes": 0, "failures": 0,
            "total_latency_ms": 0.0, "last_success": 0.0,
        })
        metrics["total_calls"] += 1
        metrics["successes"] += 1
        metrics["total_latency_ms"] += latency_ms
        metrics["last_success"] = time.monotonic()

    def record_failure(
        self,
        provider: str,
        error: Optional[Exception] = None,
        reason: Optional[FailoverReason] = None,
        model: Optional[str] = None,
    ) -> None:
        """Record failed call. Trip circuit if threshold reached.

        Only trips for infrastructure errors. Logic errors are ignored.

        Args:
            provider: Provider name (e.g. "bedrock", "openai").
            error: The exception that occurred.
            reason: Classified FailoverReason. If provided, used to filter
                non-trippable errors. If None, always counts as trippable.
            model: Model id; isolates the circuit per (provider, model) so one
                overloaded model doesn't trip the whole provider (audit H2).
        """
        key = self._circuit_key(provider, model)
        # Track all failures in metrics (even non-trippable)
        metrics = self._metrics.setdefault(key, {
            "total_calls": 0, "successes": 0, "failures": 0,
            "total_latency_ms": 0.0, "last_success": 0.0,
        })
        metrics["total_calls"] += 1
        metrics["failures"] += 1

        # Filter: only trip for infrastructure errors
        if reason is not None and reason not in _TRIPPABLE_REASONS:
            return

        circuit = self._get_circuit(provider, model)
        now = time.monotonic()

        if circuit.state == CircuitState.HALF_OPEN:
            # Probe failed → back to OPEN
            circuit.state = CircuitState.OPEN
            circuit.opened_at = now
            logger.warning(
                "circuit_breaker_reopened",
                provider=key,
                error=str(error)[:200] if error else None,
                msg="HALF_OPEN → OPEN (probe failed)",
            )
            return

        # Add timestamp, prune old ones outside window
        circuit.failure_timestamps.append(now)
        circuit.failure_timestamps = [
            t for t in circuit.failure_timestamps
            if now - t <= circuit.failure_window_seconds
        ]
        circuit.failure_count = len(circuit.failure_timestamps)
        circuit.last_failure_time = now

        if circuit.failure_count >= circuit.failure_threshold:
            circuit.state = CircuitState.OPEN
            circuit.opened_at = now
            logger.error(
                "circuit_breaker_tripped",
                provider=key,
                failure_count=circuit.failure_count,
                window_seconds=circuit.failure_window_seconds,
                msg=f"CLOSED → OPEN ({circuit.failure_count} failures in {circuit.failure_window_seconds}s)",
            )

    def get_status(self) -> dict[str, dict]:
        """Get status of all circuits with health metrics."""
        result = {}
        for provider, circuit in self._circuits.items():
            metrics = self._metrics.get(provider, {})
            total = metrics.get("total_calls", 0)
            successes = metrics.get("successes", 0)
            avg_latency = (
                metrics.get("total_latency_ms", 0) / successes
                if successes > 0 else 0
            )
            result[provider] = {
                "state": circuit.state.value,
                "failure_count": circuit.failure_count,
                "failure_threshold": circuit.failure_threshold,
                "recovery_timeout_seconds": circuit.recovery_timeout_seconds,
                "metrics": {
                    "total_calls": total,
                    "successes": successes,
                    "failures": metrics.get("failures", 0),
                    "success_rate": successes / total if total > 0 else 0,
                    "avg_latency_ms": round(avg_latency, 1),
                },
            }
        return result

    def reset(self, provider: str, model: Optional[str] = None) -> None:
        """Manually reset circuits to CLOSED (admin action / token refresh).

        With `model`: resets only that (provider, model) circuit. Without it:
        resets every circuit belonging to `provider` (e.g. aws_refresher reopens
        the whole bedrock family after a successful token refresh) — including
        the bare-provider circuit and all `provider:model` siblings.
        """
        if model is not None:
            keys = [self._circuit_key(provider, model)]
        else:
            keys = [
                k for k in self._circuits
                if k == provider or k.startswith(f"{provider}:")
            ]
        for key in keys:
            circuit = self._circuits.get(key)
            if circuit is None:
                continue
            circuit.state = CircuitState.CLOSED
            circuit.failure_count = 0
            circuit.failure_timestamps.clear()
            logger.info(
                "circuit_breaker_manual_reset",
                provider=key,
                msg="manually reset to CLOSED",
            )


class CircuitOpenError(Exception):
    """Raised when attempting to call a provider/model with an open circuit."""

    def __init__(self, provider: str, model: Optional[str] = None):
        self.provider = provider
        self.model = model
        target = f"{provider}:{model}" if model else provider
        super().__init__(
            f"Circuit breaker OPEN for '{target}' — calls rejected to prevent waste"
        )


# Singleton instance
_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get or create the global circuit breaker instance.

    Uses config settings for thresholds if available.
    """
    global _circuit_breaker
    if _circuit_breaker is None:
        try:
            from app.config import get_settings
            s = get_settings()
            _circuit_breaker = CircuitBreaker(
                failure_threshold=s.circuit_breaker_failure_threshold,
                failure_window=s.circuit_breaker_failure_window,
                recovery_timeout=s.circuit_breaker_recovery_timeout,
            )
        except Exception:
            # Fallback to defaults if settings not available
            _circuit_breaker = CircuitBreaker()
    return _circuit_breaker
