"""SSE replay must use created_at (not bare sequence) so cross-task replay works.

Regression guard: `sequence` restarts at 0 per task, so a session with two
tasks has duplicate low sequence numbers. A `sequence > since` filter would
drop the second task's early events. _replay_sse_events must key off created_at.
"""

from datetime import datetime, timezone, timedelta

import app.api.v1.endpoints.chat as chat_mod


class _Row:
    def __init__(self, seq, created_at, event_type="step", data=None):
        self.sequence = seq
        self.created_at = created_at
        self.event_type = event_type
        self.data = data or {}
        import uuid
        self.id = uuid.uuid4()


class _ScalarResult:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Serves a timestamp lookup then a row list, recording the 2nd query."""
    def __init__(self, last_seen_at, replay_rows):
        self._last_seen_at = last_seen_at
        self._replay_rows = replay_rows
        self._calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        self._calls += 1
        if self._calls == 1:
            # The created_at-of-last-seen-sequence lookup.
            return _ScalarResult(value=self._last_seen_at)
        # The replay query.
        return _ScalarResult(rows=self._replay_rows)


def _install_fake(monkeypatch, session):
    import app.core.dependencies as deps
    monkeypatch.setattr(deps, "_session_factory", lambda: session, raising=False)


async def test_replay_yields_events_after_last_seen(monkeypatch):
    t0 = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    # Task 2's first event has sequence=1 (restarted!) but a later timestamp.
    rows = [
        _Row(seq=1, created_at=t0 + timedelta(seconds=10), event_type="step",
             data={"step": 1, "title": "task2 start"}),
    ]
    session = _FakeSession(last_seen_at=t0, replay_rows=rows)
    _install_fake(monkeypatch, session)

    out = []
    async for chunk in chat_mod._replay_sse_events("conv-1", since_sequence=5):
        out.append(chunk)

    # The cross-task event (sequence=1) is replayed despite 1 < 5, because the
    # filter is created_at-based. A bare `sequence > 5` filter would drop it.
    assert any("task2 start" in c for c in out)
    # And it carries an SSE id line for further resume.
    assert any(c.startswith("id: 1\n") for c in out)


async def test_replay_safe_when_no_factory(monkeypatch):
    import app.core.dependencies as deps
    monkeypatch.setattr(deps, "_session_factory", None, raising=False)
    out = [c async for c in chat_mod._replay_sse_events("conv-1", since_sequence=0)]
    assert out == []
