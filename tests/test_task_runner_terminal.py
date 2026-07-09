"""Task A2: terminal event must ALWAYS be persisted before broadcast.

Root cause being guarded:
    Frontend /chat polls the DB for a terminal event (task_completed /
    task_failed / task_cancelled). If the backend's terminal-emit path fails
    while broadcasting (or swallows the persist error with `except: pass`), the
    DB stays in `executing` forever → the frontend polls indefinitely
    ("silent death").

`TaskRunner._finalize_terminal` fixes this by:
  - persisting FIRST (mandatory, with one retry, error logged not swallowed),
  - broadcasting AFTER (best-effort),
  - being idempotent via RunningTask._terminal_emitted.

These are pure unit tests: no DB, no network. We bind the real method to a
minimal stand-in object so we can assert ordering and error semantics without
constructing a full TaskRunner.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.task_runner import RunningTask, TaskRunner


def _mk_running() -> RunningTask:
    return RunningTask(
        task_id="11111111-1111-1111-1111-111111111111",
        session_id="sess-1",
        user_id="user-1",
        message="hi",
    )


def _mk_runner_with_mocks() -> tuple[TaskRunner, AsyncMock, AsyncMock]:
    """A real TaskRunner with persist/emit/broadcast swapped for AsyncMocks."""
    runner = TaskRunner()
    persist = AsyncMock()
    emit = AsyncMock()
    runner._persist_event = persist          # type: ignore[method-assign]
    runner._emit_event = emit                # type: ignore[method-assign]
    runner._broadcast_terminal_event = AsyncMock()  # type: ignore[method-assign]
    return runner, persist, emit


@pytest.mark.asyncio
async def test_persist_called_even_when_broadcast_raises():
    """If broadcast (_emit_event) raises, the terminal event is STILL persisted."""
    runner, persist, emit = _mk_runner_with_mocks()
    emit.side_effect = RuntimeError("subscriber blew up")
    running = _mk_running()

    # Must not raise — broadcast failure is best-effort.
    await runner._finalize_terminal(running, "task_completed", {})

    assert persist.await_count == 1, "persist must be called despite broadcast failure"
    persisted_running, persisted_event = persist.await_args.args
    assert persisted_running is running
    assert persisted_event["event_type"] == "task_completed"


@pytest.mark.asyncio
async def test_persist_before_broadcast_ordering():
    """Persist must happen BEFORE any broadcast/emit (frontend polls the DB)."""
    runner, persist, emit = _mk_runner_with_mocks()
    order: list[str] = []
    persist.side_effect = lambda *a, **k: order.append("persist")
    emit.side_effect = lambda *a, **k: order.append("emit")
    runner._broadcast_terminal_event.side_effect = lambda *a, **k: order.append("broadcast")
    running = _mk_running()

    await runner._finalize_terminal(running, "task_failed", {"error": "boom"})

    assert order[0] == "persist", f"persist must run first, got order={order}"
    assert "broadcast" in order


@pytest.mark.asyncio
async def test_persist_error_is_not_silently_swallowed():
    """Persist failure must be logged (not swallowed) — retried then surfaced."""
    runner, persist, emit = _mk_runner_with_mocks()
    persist.side_effect = RuntimeError("db down")
    running = _mk_running()
    logged: list[tuple] = []
    # Patch module logger.error to capture the surfaced error.
    import app.core.task_runner as tr
    orig_error = tr.logger.error
    tr.logger.error = lambda *a, **k: logged.append((a, k))  # type: ignore[assignment]
    try:
        # Must not raise into the caller (task finally-block must still run),
        # but the failure MUST be logged.
        await runner._finalize_terminal(running, "task_completed", {})
    finally:
        tr.logger.error = orig_error  # type: ignore[assignment]

    # Retry: persist attempted twice (initial + 1 retry).
    assert persist.await_count >= 2, "persist must be retried on failure"
    assert logged, "persist failure must be logged, never silently swallowed"


@pytest.mark.asyncio
async def test_finalize_is_idempotent():
    """Second call is a no-op (terminal already emitted)."""
    runner, persist, emit = _mk_runner_with_mocks()
    running = _mk_running()

    await runner._finalize_terminal(running, "task_completed", {})
    await runner._finalize_terminal(running, "task_completed", {})

    assert running._terminal_emitted is True
    assert persist.await_count == 1, "idempotent: persist only once"
