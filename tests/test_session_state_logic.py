"""Unit tests for the pure is_running decision logic in session_state.

These exercise `decide_is_running` in isolation — no DB, no AsyncClient, no
runner. They lock in the priority rules:

1. A persisted terminal event is the DEFINITIVE arbiter — it beats an
   in-memory active task.
2. An in-memory active task (no terminal event) → running.
3. No memory but a non-terminal DB row with a fresh heartbeat → running.
4. A stale heartbeat → not running.
"""

from __future__ import annotations

from app.api.v1.endpoints.session_state import decide_is_running

_STALE = 90.0


def test_terminal_event_beats_memory_active():
    """Rule 1: terminal event wins even when memory says the task is active."""
    assert decide_is_running(
        has_terminal_event=True,
        memory_active=True,
        db_status="executing",
        heartbeat_idle_seconds=0.0,
        stale_threshold=_STALE,
    ) is False


def test_memory_active_no_terminal_is_running():
    """Rule 2: in-memory active task with no terminal event → running."""
    assert decide_is_running(
        has_terminal_event=False,
        memory_active=True,
        db_status=None,
        heartbeat_idle_seconds=None,
        stale_threshold=_STALE,
    ) is True


def test_no_memory_fresh_heartbeat_is_running():
    """Rule 3: no memory, non-terminal DB row, fresh heartbeat → running."""
    assert decide_is_running(
        has_terminal_event=False,
        memory_active=False,
        db_status="executing",
        heartbeat_idle_seconds=10.0,
        stale_threshold=_STALE,
    ) is True


def test_no_memory_stale_heartbeat_not_running():
    """Rule 4: heartbeat older than the stale threshold → not running."""
    assert decide_is_running(
        has_terminal_event=False,
        memory_active=False,
        db_status="executing",
        heartbeat_idle_seconds=120.0,
        stale_threshold=_STALE,
    ) is False


def test_terminal_db_status_not_running():
    """A terminal DB status is not 'executing' → not running (rule 4 fallthrough)."""
    assert decide_is_running(
        has_terminal_event=False,
        memory_active=False,
        db_status="completed",
        heartbeat_idle_seconds=5.0,
        stale_threshold=_STALE,
    ) is False


def test_no_signals_not_running():
    """Nothing active anywhere → not running."""
    assert decide_is_running(
        has_terminal_event=False,
        memory_active=False,
        db_status=None,
        heartbeat_idle_seconds=None,
        stale_threshold=_STALE,
    ) is False


def test_stale_heartbeat_but_fresh_event_is_running():
    """Root-cause fix: a dropped heartbeat must NOT read as dead while the
    orchestrator is still emitting events. A fresh task_event keeps it alive."""
    assert decide_is_running(
        has_terminal_event=False,
        memory_active=False,
        db_status="executing",
        heartbeat_idle_seconds=300.0,  # heartbeat write was dropped under load
        stale_threshold=_STALE,
        recent_event_idle_seconds=5.0,  # but events are flowing
    ) is True


def test_stale_heartbeat_and_stale_event_not_running():
    """Both liveness signals stale → genuinely dead → not running."""
    assert decide_is_running(
        has_terminal_event=False,
        memory_active=False,
        db_status="executing",
        heartbeat_idle_seconds=300.0,
        stale_threshold=_STALE,
        recent_event_idle_seconds=300.0,
    ) is False


def test_terminal_event_beats_fresh_event():
    """A terminal event still wins even if a (stray) recent event exists."""
    assert decide_is_running(
        has_terminal_event=True,
        memory_active=False,
        db_status="executing",
        heartbeat_idle_seconds=300.0,
        stale_threshold=_STALE,
        recent_event_idle_seconds=1.0,
    ) is False
