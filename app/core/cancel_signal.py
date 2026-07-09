"""Cross-process cancel signalling helpers (pure functions).

The cancel endpoint runs on whatever Uvicorn worker the HTTP request landed
on, but the task may be executing on a different worker (UVICORN_WORKERS=2).
In-memory cancellation tokens are per-process, so the endpoint publishes a
cancel message on a Redis pub/sub channel; every worker subscribes and the
one that OWNS the task performs the real cancellation. A durable DB flag
(`metadata.cancel_requested`) is the backstop in case the pub/sub message is
missed or the token was not yet registered.

This module holds only pure data/decision helpers so they can be unit-tested
without Redis or a DB — mirroring app/core/task_watchdog.is_task_dead().
"""

from __future__ import annotations

# Redis pub/sub channel that every worker subscribes to for cancel requests.
CANCEL_CHANNEL = "nexus:cancel"

# States from which a task may be resumed from its last checkpoint. MUST stay
# in sync with the /tasks/{id}/resume endpoint guard. "executing/planning/
# decomposing" are included because a crashed/restarted server leaves rows in
# those states (the startup reconciler flips them to suspended, but the list
# tolerates the race).
RESUMABLE_STATES: tuple[str, ...] = (
    "executing",
    "planning",
    "decomposing",
    "suspended",
    "cancelled",
    "failed",
    "error",
    "crashed",
)


def build_cancel_payload(task_id: str, session_id: str | None) -> dict:
    """Build the pub/sub envelope published on CANCEL_CHANNEL."""
    return {"task_id": task_id, "session_id": session_id}


def parse_cancel_payload(payload: dict) -> tuple[str | None, str | None]:
    """Extract (task_id, session_id) from a cancel envelope.

    Returns (None, None) when task_id is absent so subscribers can ignore
    malformed messages without raising.
    """
    task_id = payload.get("task_id")
    if not task_id:
        return None, None
    return task_id, payload.get("session_id")


def should_keep_cancelled(current_status: str, cancel_requested: bool) -> bool:
    """Decide whether a terminal-status write must preserve 'cancelled'.

    Called right before the task runner / pipeline writes a terminal status.
    A normal-looking 'completed' must NOT clobber a cancellation that was
    requested while the final turn was draining.
    """
    return current_status == "cancelled" or cancel_requested


def clear_cancel_flags(metadata: dict) -> dict:
    """Return a copy of metadata with the durable cancel flags removed.

    Called by /tasks/{id}/resume: a resumed task must NOT carry a leftover
    cancel_requested flag, or the pipeline's DB-flag backstop (_is_cancelled_db)
    would re-cancel it at the first turn boundary. Returns a NEW dict so the
    caller can reassign task.metadata_ and have SQLAlchemy flag the JSON column
    dirty (in-place mutation of a JSON column is not reliably tracked).
    """
    cleaned = dict(metadata or {})
    cleaned.pop("cancel_requested", None)
    cleaned.pop("cancel_requested_at", None)
    return cleaned
