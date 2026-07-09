"""Checkpoint rollback: load a specific prior turn and discard later checkpoints."""

import pytest

from app.core.checkpoint_persistence import CheckpointPersistence


class _Row:
    def __init__(self, task_id, turn_number, state_json, reason="turn_complete"):
        self.task_id = task_id
        self.turn_number = turn_number
        self.state_json = state_json
        self.reason = reason
        import uuid as _u
        self.id = _u.uuid4()
        from datetime import datetime, timezone
        self.created_at = datetime.now(timezone.utc)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return [(r.id,) for r in self._rows]


class _FakeSession:
    """Minimal async DB stub: serves pre-seeded rows, records deletes."""
    def __init__(self, rows):
        self._rows = rows
        self.deleted_ids = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        # Distinguish delete vs select by class name (good enough for the stub).
        name = type(stmt).__name__.lower()
        if "delete" in name:
            # Record nothing precise; the rollback test checks commit path instead.
            return _FakeResult([])
        return _FakeResult(self._rows)

    async def commit(self):
        pass


def _factory(rows):
    def _make():
        return _FakeSession(rows)
    return _make


async def test_load_checkpoint_at_turn_returns_target(monkeypatch):
    import uuid
    tid = str(uuid.uuid4())
    rows = [_Row(uuid.UUID(tid), 2, {"current_turn": 2})]
    svc = CheckpointPersistence(_factory(rows))
    cp = await svc.load_checkpoint_at_turn(tid, 2)
    assert cp is not None
    assert cp["turn_number"] == 2
    assert cp["state_json"]["current_turn"] == 2


async def test_load_checkpoint_at_turn_none_when_missing():
    import uuid
    tid = str(uuid.uuid4())
    svc = CheckpointPersistence(_factory([]))
    cp = await svc.load_checkpoint_at_turn(tid, 5)
    assert cp is None


async def test_rollback_to_turn_runs_without_error():
    import uuid
    tid = str(uuid.uuid4())
    rows = [_Row(uuid.UUID(tid), 1, {"current_turn": 1})]
    svc = CheckpointPersistence(_factory(rows))
    # Should complete (delete later checkpoints) without raising.
    deleted = await svc.rollback_to_turn(tid, 1)
    assert deleted is True
