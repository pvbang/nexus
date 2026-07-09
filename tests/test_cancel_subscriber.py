"""The cancel pub/sub handler cancels only when THIS worker owns the task.

A worker that does not own the task must no-op gracefully (return False) so the
broadcast is harmless. The owning worker returns True after cancelling.
"""

from __future__ import annotations

import app.core.cancel_subscriber as cs


async def test_handler_ignores_malformed_payload():
    assert await cs.handle_cancel_message({}) is False
    assert await cs.handle_cancel_message({"session_id": "s"}) is False


async def test_handler_returns_false_when_not_owner(monkeypatch):
    class _Runner:
        async def cancel_task(self, session_id):
            return False  # this worker does not own the task

    monkeypatch.setattr(cs, "get_task_runner", lambda: _Runner())
    # token set is best-effort; cancel_task False ⇒ handler reports not-owned
    assert await cs.handle_cancel_message({"task_id": "t1", "session_id": "s1"}) is False


async def test_handler_returns_true_when_owner(monkeypatch):
    class _Runner:
        async def cancel_task(self, session_id):
            return True  # owner — really cancelled

    monkeypatch.setattr(cs, "get_task_runner", lambda: _Runner())
    assert await cs.handle_cancel_message({"task_id": "t2", "session_id": "s2"}) is True
