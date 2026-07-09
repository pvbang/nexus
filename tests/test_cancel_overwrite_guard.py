"""A draining 'completed' write must never clobber a user cancellation.

The runner finishes the final turn and tries to mark the task 'completed', but
the user pressed Cancel while that turn was draining. _update_task_db_status
re-reads the row and, via should_keep_cancelled, skips the overwrite.
"""

from __future__ import annotations

from app.core.cancel_signal import should_keep_cancelled


def test_completed_blocked_when_db_already_cancelled():
    assert should_keep_cancelled("cancelled", cancel_requested=False) is True


def test_completed_blocked_when_cancel_requested_flag_set():
    assert should_keep_cancelled("executing", cancel_requested=True) is True


def test_completed_allowed_on_clean_finish():
    assert should_keep_cancelled("executing", cancel_requested=False) is False
