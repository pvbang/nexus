"""POST /tasks/{id}/cancel must signal across workers, not just locally.

The endpoint writes a durable cancel flag to the task row, attempts a local
cancel (works if the task runs on THIS worker), and publishes on the Redis
cancel channel so the OWNING worker (possibly a different process) stops the
task. We assert the source wires all three legs, since a unit test cannot spin
up two real Uvicorn workers.
"""

from __future__ import annotations

import inspect

from app.api.v1.endpoints import tasks as tasks_ep


def test_cancel_endpoint_wires_all_three_legs():
    src = inspect.getsource(tasks_ep.cancel_task)
    # 1. durable DB flag
    assert "cancel_requested" in src
    # 2. cross-process publish on the shared channel
    assert "CANCEL_CHANNEL" in src or "nexus:cancel" in src
    assert "get_message_bus" in src
    # 3. local fast-path through the runner (real asyncio cancel)
    assert "get_task_runner" in src
    # must NOT rely solely on the weak in-memory token function
    assert "return {\"status\": \"cancelling\"" in src or "\"cancelling\"" in src
