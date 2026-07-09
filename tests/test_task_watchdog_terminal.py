"""Unit tests for the watchdog terminal-event builder (Task A3).

When the watchdog marks a dead task as "crashed", it must also persist a
terminal "task_failed" event into task_events so the frontend /chat polling
stops waiting. The event payload is built by a pure function so it can be
tested without a DB.

Pure-function tests only (no DB, no network) — fast and deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.task_watchdog import build_stale_terminal_event, is_task_dead


_CUTOFF = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)


def test_dead_when_no_events_at_all():
    """A stale-heartbeat task that never emitted an event is dead."""
    assert is_task_dead(last_event_at=None, cutoff=_CUTOFF) is True


def test_alive_when_recent_event_after_cutoff():
    """Root-cause fix: a stale heartbeat but a fresh event → task is alive."""
    fresh = _CUTOFF + timedelta(seconds=30)
    assert is_task_dead(last_event_at=fresh, cutoff=_CUTOFF) is False


def test_dead_when_last_event_older_than_cutoff():
    """No heartbeat AND no recent event → genuinely dead."""
    old = _CUTOFF - timedelta(seconds=30)
    assert is_task_dead(last_event_at=old, cutoff=_CUTOFF) is True


def test_naive_event_timestamp_treated_as_utc():
    """A naive datetime (no tzinfo) must not raise and is compared as UTC."""
    naive_fresh = (_CUTOFF + timedelta(seconds=10)).replace(tzinfo=None)
    assert is_task_dead(last_event_at=naive_fresh, cutoff=_CUTOFF) is False


def test_event_type_is_task_failed():
    evt = build_stale_terminal_event(
        task_id="t-1", session_id="s-1", sequence=5, timeout_seconds=120
    )
    assert evt["event_type"] == "task_failed"


def test_resumable_is_true():
    evt = build_stale_terminal_event(
        task_id="t-1", session_id="s-1", sequence=5, timeout_seconds=120
    )
    assert evt["data"]["resumable"] is True


def test_reason_is_stale_heartbeat():
    evt = build_stale_terminal_event(
        task_id="t-1", session_id="s-1", sequence=5, timeout_seconds=120
    )
    assert evt["data"]["reason"] == "stale_heartbeat"


def test_carries_identifiers_and_sequence():
    evt = build_stale_terminal_event(
        task_id="t-42", session_id="sess-9", sequence=7, timeout_seconds=120
    )
    assert evt["task_id"] == "t-42"
    assert evt["session_id"] == "sess-9"
    assert evt["sequence"] == 7


def test_error_describes_stale_heartbeat_with_timeout():
    evt = build_stale_terminal_event(
        task_id="t-1", session_id="s-1", sequence=0, timeout_seconds=120
    )
    error = evt["data"]["error"]
    assert isinstance(error, str)
    assert error  # non-empty
    assert "120" in error  # timeout reflected in the message
