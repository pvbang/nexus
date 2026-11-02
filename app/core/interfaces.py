"""
Nexus - Service Interfaces (Protocols).

Inspired by Shannon's ActivityLogger interface pattern.
Keeps services framework-agnostic — testable, mockable, swappable.

These Protocols define contracts that services depend on,
without importing concrete implementations.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class ServiceLogger(Protocol):
    """
    Logger interface for services.

    Services accept this instead of importing structlog directly.
    This enables:
    - Unit testing with mock loggers
    - Different logging backends per context
    - Structured logging without framework coupling
    """

    def info(self, message: str, **kwargs: Any) -> None:
        """Log informational message."""
        ...

    def warn(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        ...

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        ...

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        ...


@runtime_checkable
class EventEmitter(Protocol):
    """
    Interface for emitting events to frontend/subscribers.

    Services use this to send progress updates without knowing
    about WebSocket, SSE, or any transport details.
    """

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event to subscribers."""
        ...


@runtime_checkable
class LLMProvider(Protocol):
    """
    Interface for LLM providers.

    Allows services to call LLMs without coupling to specific provider SDK.
    """

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Send a chat completion request."""
        ...


@runtime_checkable
class CheckpointProvider(Protocol):
    """
    Interface for task checkpoint persistence.

    Allows saving/loading execution state for resume capability.
    """

    async def save(self, task_id: str, state: dict[str, Any]) -> None:
        """Save checkpoint state for a task."""
        ...

    async def load(self, task_id: str) -> Optional[dict[str, Any]]:
        """Load checkpoint state for a task. Returns None if not found."""
        ...

    async def delete(self, task_id: str) -> None:
        """Delete checkpoint state for a task."""
        ...


@runtime_checkable
class AuditWriter(Protocol):
    """
    Interface for audit logging — append-only structured event log.

    Separate from ServiceLogger: audit is for compliance/debugging traces,
    not operational logging.
    """

    async def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Append an audit event."""
        ...

    async def log_tool_call(
        self, tool_name: str, params: dict[str, Any], result: Any, duration_ms: float
    ) -> None:
        """Log a tool invocation with timing."""
        ...

    async def log_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
        cost_usd: Optional[float] = None,
    ) -> None:
        """Log an LLM call with metrics."""
        ...

    async def log_error(self, error: Exception, context: Optional[str] = None) -> None:
        """Log an error event."""
        ...


# --- Concrete Adapters ---


class StructlogServiceLogger:
    """
    Adapter: wraps structlog logger to satisfy ServiceLogger protocol.

    Usage:
        import structlog
        logger = StructlogServiceLogger(structlog.get_logger("my_service"))
    """

    def __init__(self, logger: Any) -> None:
        self._logger = logger

    def info(self, message: str, **kwargs: Any) -> None:
        self._logger.info(message, **kwargs)

    def warn(self, message: str, **kwargs: Any) -> None:
        self._logger.warning(message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        self._logger.error(message, **kwargs)

    def debug(self, message: str, **kwargs: Any) -> None:
        self._logger.debug(message, **kwargs)


class NullLogger:
    """
    No-op logger for testing or when logging is not needed.

    Usage in tests:
        service = MyService(logger=NullLogger())
    """

    def info(self, message: str, **kwargs: Any) -> None:
        pass

    def warn(self, message: str, **kwargs: Any) -> None:
        pass

    def error(self, message: str, **kwargs: Any) -> None:
        pass

    def debug(self, message: str, **kwargs: Any) -> None:
        pass


class CollectingLogger:
    """
    Logger that collects all messages for assertion in tests.

    Usage in tests:
        logger = CollectingLogger()
        service = MyService(logger=logger)
        await service.do_work()
        assert any("success" in m for m in logger.messages)
    """

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, message: str, **kwargs: Any) -> None:
        self.messages.append(("info", message, kwargs))

    def warn(self, message: str, **kwargs: Any) -> None:
        self.messages.append(("warn", message, kwargs))

    def error(self, message: str, **kwargs: Any) -> None:
        self.messages.append(("error", message, kwargs))

    def debug(self, message: str, **kwargs: Any) -> None:
        self.messages.append(("debug", message, kwargs))
