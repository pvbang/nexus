"""CheckpointPersistence must report success honestly, never assume it.

Root cause of the shutdown FK incident: graceful_shutdown passed a session_id
(the _running_tasks key) where a task_id was required. task_checkpoints.task_id
FKs tasks.id, so every save raised ForeignKeyViolationError — but save_checkpoint
swallowed it and returned None, so the shutdown loop still logged
"shutdown_checkpoints_saved count=N". The fix routes the real task_id AND makes
save_checkpoint/mark_task_suspended return a bool so callers stop claiming saves
that never happened.

These tests pin the bool contract with a fake async session (no real DB), the
same no-DB style the other checkpoint/cancel helpers are tested with.
"""

from __future__ import annotations

import asyncio

from app.core.checkpoint_persistence import CheckpointPersistence
from app.orchestration.models import ExecutionState


class _FakeResult:
    def __init__(self, rows=None, rowcount=0):
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Minimal async-context session. flush_raises simulates the FK violation."""

    def __init__(self, *, flush_raises=False, update_rowcount=1):
        self.flush_raises = flush_raises
        self.update_rowcount = update_rowcount
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, _obj):
        pass

    async def flush(self):
        if self.flush_raises:
            raise RuntimeError("FK violation: task_id not present in table tasks")

    async def execute(self, _stmt):
        # save_checkpoint's prune SELECT -> no old rows; mark_task_suspended UPDATE
        return _FakeResult(rows=[], rowcount=self.update_rowcount)

    async def commit(self):
        self.committed = True


def _factory(session):
    return lambda: session


def _state():
    return ExecutionState(task_id="11111111-1111-1111-1111-111111111111", user_message="hi")


def test_save_checkpoint_returns_true_on_success():
    session = _FakeSession()
    svc = CheckpointPersistence(_factory(session))
    ok = asyncio.run(svc.save_checkpoint("11111111-1111-1111-1111-111111111111", 0, _state()))
    assert ok is True
    assert session.committed is True


def test_save_checkpoint_returns_false_on_fk_violation_without_raising():
    session = _FakeSession(flush_raises=True)
    svc = CheckpointPersistence(_factory(session))
    # Must NOT raise — the shutdown loop relies on this being best-effort.
    ok = asyncio.run(svc.save_checkpoint("deadbeef-dead-dead-dead-deaddeaddead", 0, _state()))
    assert ok is False
    assert session.committed is False


def test_mark_task_suspended_true_when_row_updated():
    session = _FakeSession(update_rowcount=1)
    svc = CheckpointPersistence(_factory(session))
    ok = asyncio.run(svc.mark_task_suspended("11111111-1111-1111-1111-111111111111"))
    assert ok is True


def test_mark_task_suspended_false_when_no_matching_row():
    session = _FakeSession(update_rowcount=0)
    svc = CheckpointPersistence(_factory(session))
    ok = asyncio.run(svc.mark_task_suspended("11111111-1111-1111-1111-111111111111"))
    assert ok is False
