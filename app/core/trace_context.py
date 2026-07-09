"""Trace correlation context for the orchestration pipeline (audit C4).

Problem: logs had no way to correlate a request → task → agent → tool. When two
tasks run concurrently their log lines interleave with nothing tying each line to
its task/turn/agent, making production debugging guesswork.

Solution: bind a small set of correlation fields into structlog's contextvars.
The logging pipeline already runs ``structlog.contextvars.merge_contextvars``
(see ``app/core/logging.py``), so anything bound here is automatically attached to
EVERY ``structlog`` log line emitted in the same async context — no per-call
plumbing required.

Why contextvars: ``asyncio`` copies the current context when a task is created, so
a ``trace_id`` bound at the top of ``pipeline.run`` is inherited by every agent
coroutine and tool call spawned beneath it. Each layer can add its own fields
(``agent_role``, ``turn``) without clobbering the parent's.

Usage::

    bind_trace(task_id=tid)                  # start of a task — generates trace_id
    ...
    with agent_scope("developer", turn=3):   # per-agent span, auto-reverted
        ...
    clear_trace()                            # end of task
"""

from __future__ import annotations

import contextlib
import uuid

import structlog

# Keys this module manages in the structlog contextvars dict. Centralised so
# clear_trace() removes exactly what was bound and nothing else.
_TRACE_KEYS = ("trace_id", "task_id", "agent_role", "turn")


def new_trace_id() -> str:
    """Generate a short, log-friendly trace id."""
    return uuid.uuid4().hex[:12]


def bind_trace(task_id: str, trace_id: str | None = None) -> str:
    """Begin a trace for a task. Returns the trace_id (generated if not given).

    Binds ``trace_id`` and ``task_id`` so every subsequent log line in this async
    context carries them. Call once at the start of task execution.
    """
    tid = trace_id or new_trace_id()
    structlog.contextvars.bind_contextvars(trace_id=tid, task_id=task_id)
    return tid


def bind_agent(agent_role: str, turn: int | None = None) -> None:
    """Attach the current agent_role (and optional turn) to the trace context."""
    if turn is None:
        structlog.contextvars.bind_contextvars(agent_role=agent_role)
    else:
        structlog.contextvars.bind_contextvars(agent_role=agent_role, turn=turn)


def bind_turn(turn: int) -> None:
    """Attach the current turn number to the trace context."""
    structlog.contextvars.bind_contextvars(turn=turn)


def get_trace_id() -> str | None:
    """Return the trace_id bound to the current context, if any."""
    return structlog.contextvars.get_contextvars().get("trace_id")


# Standard HTTP header names for cross-service trace propagation.
TRACE_ID_HEADER = "X-Trace-Id"
TASK_ID_HEADER = "X-Task-Id"


def trace_headers() -> dict[str, str]:
    """Build HTTP headers that propagate the current trace across services.

    Returns ``{}`` when no trace is bound, so callers can ``headers.update(...)``
    unconditionally. Lets a downstream service (e.g. OpenClaw) log under the same
    trace_id, giving end-to-end correlation across the service boundary (audit C4).
    """
    ctx = structlog.contextvars.get_contextvars()
    headers: dict[str, str] = {}
    if ctx.get("trace_id"):
        headers[TRACE_ID_HEADER] = str(ctx["trace_id"])
    if ctx.get("task_id"):
        headers[TASK_ID_HEADER] = str(ctx["task_id"])
    return headers


def clear_trace() -> None:
    """Remove all trace keys this module manages from the current context."""
    structlog.contextvars.unbind_contextvars(*_TRACE_KEYS)


@contextlib.contextmanager
def agent_scope(agent_role: str, turn: int | None = None):
    """Scoped agent span: binds agent_role/turn, restores prior values on exit.

    Use around a single agent's execution so its log lines are tagged without
    leaking the binding to sibling agents that share the parent context.
    """
    prior = structlog.contextvars.get_contextvars()
    bind_agent(agent_role, turn)
    try:
        yield
    finally:
        # Restore the exact prior state for the keys we touched.
        for key in ("agent_role", "turn"):
            if key in prior:
                structlog.contextvars.bind_contextvars(**{key: prior[key]})
            else:
                structlog.contextvars.unbind_contextvars(key)
