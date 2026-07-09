"""Graceful-shutdown checkpoints must use the real task_id, not the dict key.

`TaskRunner._running_tasks` is keyed by session_id (conversation_id). The
shutdown loop in `lifespan` previously passed that key straight into
`save_checkpoint(task_id=...)`, but `task_checkpoints.task_id` has a foreign
key to `tasks.id` — a session_id is not a task_id, so every shutdown checkpoint
raised ForeignKeyViolationError. The real task_id lives on the RunningTask.
"""

from __future__ import annotations

import inspect

from app import main as app_main


def test_shutdown_checkpoint_uses_rtask_task_id_not_dict_key():
    src = inspect.getsource(app_main.lifespan)

    # Isolate the graceful-shutdown checkpoint block.
    start = src.index("Graceful Shutdown: Checkpoint all running tasks")
    block = src[start:start + 2000]

    # The real task_id must be sourced from the RunningTask value.
    assert "task_id=_rtask.task_id" in block
    assert "_ckpt_svc.mark_task_suspended(_rtask.task_id)" in block

    # The dict key (session_id) must never be passed as a task_id again.
    assert "task_id=_tid" not in block
    assert "mark_task_suspended(_tid)" not in block


def test_shutdown_only_counts_checkpoints_that_actually_persisted():
    """count must reflect real saves — save_checkpoint returns a bool now, so
    the loop must gate _ckpt_count (and the suspend) on it. Otherwise the log
    'shutdown_checkpoints_saved count=N' lies when every save hit the FK."""
    src = inspect.getsource(app_main.lifespan)
    start = src.index("Graceful Shutdown: Checkpoint all running tasks")
    block = src[start:start + 2000]

    assert "_saved = await _ckpt_svc.save_checkpoint(" in block
    assert "if _saved:" in block
    # The unconditional increment is gone.
    assert "reason=\"graceful_shutdown\",\n                            )\n                            await _ckpt_svc.mark_task_suspended" not in block
