"""Tests for trace correlation context (audit C4).

Verifies that trace fields land in structlog's contextvars (and thus on every log
line via merge_contextvars), that asyncio tasks inherit the parent trace, and that
agent_scope restores prior state without leaking to siblings.
"""

from __future__ import annotations

import asyncio

import pytest
import structlog

from app.core import trace_context as tc


@pytest.fixture(autouse=True)
def _clean_contextvars():
    """Each test starts and ends with empty structlog contextvars."""
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def test_bind_trace_sets_trace_and_task_id():
    tid = tc.bind_trace(task_id="task-1")
    ctx = structlog.contextvars.get_contextvars()
    assert ctx["trace_id"] == tid
    assert ctx["task_id"] == "task-1"
    assert len(tid) == 12


def test_bind_trace_honours_explicit_id():
    tid = tc.bind_trace(task_id="task-1", trace_id="fixedtrace01")
    assert tid == "fixedtrace01"
    assert tc.get_trace_id() == "fixedtrace01"


def test_trace_headers_empty_when_no_trace():
    assert tc.trace_headers() == {}


def test_trace_headers_carry_trace_and_task_id():
    tc.bind_trace(task_id="task-1", trace_id="trace0000abc")
    headers = tc.trace_headers()
    assert headers[tc.TRACE_ID_HEADER] == "trace0000abc"
    assert headers[tc.TASK_ID_HEADER] == "task-1"


def test_trace_headers_omit_missing_task_id():
    # trace_id bound directly without task_id → only the trace header present.
    structlog.contextvars.bind_contextvars(trace_id="abc123def456")
    headers = tc.trace_headers()
    assert headers[tc.TRACE_ID_HEADER] == "abc123def456"
    assert tc.TASK_ID_HEADER not in headers


def test_clear_trace_removes_all_managed_keys():
    tc.bind_trace(task_id="task-1")
    tc.bind_agent("developer", turn=2)
    tc.clear_trace()
    ctx = structlog.contextvars.get_contextvars()
    for key in ("trace_id", "task_id", "agent_role", "turn"):
        assert key not in ctx


def test_bind_agent_with_and_without_turn():
    tc.bind_trace(task_id="t")
    tc.bind_agent("researcher")
    assert structlog.contextvars.get_contextvars()["agent_role"] == "researcher"
    assert "turn" not in structlog.contextvars.get_contextvars()
    tc.bind_agent("writer", turn=5)
    ctx = structlog.contextvars.get_contextvars()
    assert ctx["agent_role"] == "writer"
    assert ctx["turn"] == 5


# ───────────────────────── agent_scope ─────────────────────────


def test_agent_scope_restores_absent_keys():
    tc.bind_trace(task_id="t")  # no agent_role yet
    with tc.agent_scope("developer", turn=1):
        assert structlog.contextvars.get_contextvars()["agent_role"] == "developer"
    # After the scope, agent_role/turn must be gone again.
    ctx = structlog.contextvars.get_contextvars()
    assert "agent_role" not in ctx
    assert "turn" not in ctx
    assert ctx["trace_id"]  # trace survives


def test_agent_scope_restores_prior_values():
    tc.bind_trace(task_id="t")
    tc.bind_agent("orchestrator", turn=0)
    with tc.agent_scope("developer", turn=3):
        assert structlog.contextvars.get_contextvars()["agent_role"] == "developer"
        assert structlog.contextvars.get_contextvars()["turn"] == 3
    # Prior values restored exactly.
    ctx = structlog.contextvars.get_contextvars()
    assert ctx["agent_role"] == "orchestrator"
    assert ctx["turn"] == 0


# ───────────────────────── asyncio inheritance ─────────────────────────


@pytest.mark.asyncio
async def test_child_task_inherits_trace_but_does_not_leak_back():
    tid = tc.bind_trace(task_id="parent-task")

    seen = {}

    async def child():
        # Child inherits the parent's bound context at creation time.
        seen["trace_id"] = tc.get_trace_id()
        # A binding made inside the child must not leak to the parent.
        tc.bind_agent("child-agent")

    await asyncio.create_task(child())

    assert seen["trace_id"] == tid
    # Parent context is unaffected by the child's bind_agent.
    assert "agent_role" not in structlog.contextvars.get_contextvars()


# ───────────────────────── end-to-end: fields reach the log line ─────────────────────────


def test_trace_fields_appear_on_actual_log_event():
    """The real proof: bound fields must land on an emitted structlog event.

    Uses merge_contextvars (the same processor app/core/logging.py installs) so
    this exercises the actual mechanism, not just the contextvar store.
    """
    cap = structlog.testing.LogCapture()
    structlog.configure(
        processors=[structlog.contextvars.merge_contextvars, cap],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=False,
    )
    try:
        tc.bind_trace(task_id="task-xyz", trace_id="trace0000abc")
        tc.bind_agent("developer", turn=7)
        structlog.get_logger("test").info("did_something")

        assert len(cap.entries) == 1
        entry = cap.entries[0]
        assert entry["trace_id"] == "trace0000abc"
        assert entry["task_id"] == "task-xyz"
        assert entry["agent_role"] == "developer"
        assert entry["turn"] == 7
        assert entry["event"] == "did_something"
    finally:
        structlog.reset_defaults()
