"""Lightweight, dependency-free tracing — trace_id correlation + spans.

nexus already exposes Prometheus metrics and propagates session/task/agent
via contextvars, but had no way to correlate a single run's work into a trace
of timed spans (the observability gap flagged in the 2026-06 audit).

This module provides:
- a process-wide ``trace_id`` carried in a contextvar (one id per run),
- a ``Tracer`` that yields timed ``Span`` context managers,
- a pluggable ``exporter`` callback: default is no-op (zero overhead, no deps);
  pass a collector to record, or wire a Langfuse/OTel exporter later.

Design rules: never raise into traced code; an exporter that throws is
swallowed. Mirrors deer-flow's root-level tracing idea without forcing a
network dependency.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)

_current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_trace_id", default=""
)


def new_trace_id() -> str:
    """Generate a fresh trace id (hex, 32 chars)."""
    return uuid.uuid4().hex


def get_current_trace_id() -> str:
    return _current_trace_id.get()


def set_current_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)


def _trace_context_id() -> str:
    """Best-effort read of the pipeline's structlog trace_id (audit C4)."""
    try:
        from app.core.trace_context import get_trace_id
        return get_trace_id() or ""
    except Exception:
        return ""


class Span:
    """A single timed unit of work within a trace."""

    def __init__(self, name: str, attrs: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.attributes: dict[str, Any] = dict(attrs or {})
        self.status = "ok"
        # Prefer this module's contextvar; fall back to the pipeline's
        # structlog trace_context (audit C4) so spans share one trace id.
        self.trace_id = get_current_trace_id() or _trace_context_id()
        self._start = 0.0
        self.duration_ms = 0.0

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def to_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "attributes": dict(self.attributes),
        }


class _SpanContext:
    """Context manager that times a span and exports it on exit."""

    def __init__(self, span: Span, exporter: Optional[Callable[[dict], None]]) -> None:
        self._span = span
        self._exporter = exporter

    def __enter__(self) -> Span:
        self._span._start = time.monotonic()
        return self._span

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._span.duration_ms = round((time.monotonic() - self._span._start) * 1000, 3)
        if exc is not None:
            self._span.status = "error"
            self._span.attributes.setdefault("error", str(exc))
        if self._exporter is not None:
            try:
                self._exporter(self._span.to_record())
            except Exception:
                logger.debug("trace_export_failed", span=self._span.name, exc_info=True)
        return False  # never suppress exceptions


class Tracer:
    """Creates spans and routes them to an optional exporter (no-op by default)."""

    def __init__(self, exporter: Optional[Callable[[dict], None]] = None) -> None:
        self._exporter = exporter
        # Convenience in-memory sink for tests/inspection when no exporter given.
        self.exported: list[dict[str, Any]] = []
        if exporter is None:
            self._exporter = None

    def span(self, name: str, attrs: Optional[dict[str, Any]] = None) -> _SpanContext:
        return _SpanContext(Span(name, attrs), self._exporter)


def _structlog_exporter(record: dict[str, Any]) -> None:
    """Default exporter: emit each span as a structured debug log line."""
    logger.debug(
        "trace_span",
        span=record["name"],
        trace_id=record["trace_id"],
        duration_ms=record["duration_ms"],
        status=record["status"],
        **{f"attr_{k}": v for k, v in record["attributes"].items()},
    )


_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """Process-wide tracer. Spans are logged via structlog by default.

    A future Langfuse/OTel exporter can replace ``_structlog_exporter`` without
    touching call sites.
    """
    global _tracer
    if _tracer is None:
        _tracer = Tracer(exporter=_structlog_exporter)
    return _tracer
