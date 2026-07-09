"""ACTIVITY.md terminal marker must reflect the REAL terminal state (audit S4).

Cross-session audit 2026-06-30: ``log_task_complete`` printed "## ✅ Task Completed"
UNCONDITIONALLY at the end of the loop. The deepseek run halted via
``evolution_terminate`` (TODO.md correctly wrote "⚠ HALTED") yet ACTIVITY.md still
stamped "✅ Task Completed"; the Opus 4.8 run ended on an eval-parse-fail with the
decision still CONTINUE @50%, again stamped "✅ Task Completed". The two records
contradicted each other.

The marker must distinguish: a genuine completion (✅), an honest halt with a
terminated_reason (⚠ HALTED), and a loop that stopped without completing and
without a halt reason (⏳ Stopped, e.g. budget/turn exhaustion with decision
still 'continue').
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def logger_for(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    import app.orchestration.activity_logger as al
    importlib.reload(al)

    def _make(task_id="acttask01"):
        return al.ActivityLogger(task_id)

    yield _make
    tp._parent_cache.clear()


def _read(lg) -> str:
    return lg._file_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_genuine_completion_marks_completed(logger_for):
    lg = logger_for()
    await lg.log_task_complete(is_complete=True, terminated_reason="")
    text = _read(lg)
    assert "✅ Task Completed" in text


@pytest.mark.asyncio
async def test_default_call_still_marks_completed(logger_for):
    # Backward-compatible default: a bare call (no args) reads as completed.
    lg = logger_for()
    await lg.log_task_complete()
    assert "✅ Task Completed" in _read(lg)


@pytest.mark.asyncio
async def test_honest_halt_marks_halted_not_completed(logger_for):
    lg = logger_for()
    await lg.log_task_complete(is_complete=True, terminated_reason="evolution_terminate")
    text = _read(lg)
    assert "✅ Task Completed" not in text
    assert "HALTED" in text.upper()
    assert "evolution_terminate" in text


@pytest.mark.asyncio
async def test_stopped_without_completion_is_not_completed(logger_for):
    # Loop ended but the task never reached completion and carries no halt reason
    # (e.g. budget/turn exhaustion with decision still 'continue').
    lg = logger_for()
    await lg.log_task_complete(is_complete=False, terminated_reason="")
    text = _read(lg)
    assert "✅ Task Completed" not in text
    assert "STOPPED" in text.upper() or "INCOMPLETE" in text.upper()
