"""The resume LIST query must accept the same states the /resume endpoint does.

Regression: get_resumable_tasks() filtered ["executing","suspended"] only, so a
just-cancelled task (status="cancelled") never appeared and the UI's "Tiếp tục"
button never showed. We assert the query's state filter is sourced from the
shared RESUMABLE_STATES constant rather than a hard-coded subset.
"""

from __future__ import annotations

import inspect

from app.core import checkpoint_persistence
from app.core.cancel_signal import RESUMABLE_STATES


def test_query_uses_shared_resumable_states():
    # The source must reference the shared constant, not a hard-coded list that
    # can drift from the /resume endpoint. This guards the root cause directly.
    src = inspect.getsource(checkpoint_persistence.CheckpointPersistence.get_resumable_tasks)
    assert "RESUMABLE_STATES" in src
    assert '["executing", "suspended"]' not in src


def test_cancelled_and_crashed_are_resumable():
    assert "cancelled" in RESUMABLE_STATES
    assert "crashed" in RESUMABLE_STATES
    assert "failed" in RESUMABLE_STATES
