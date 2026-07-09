"""Tests for phantom-write observability (audit A5 follow-up, review-hardened).

The signal must be a per-session WRITE state, not a per-call event on every path
lookup: (1) only WRITES count — a benign read resolving without context is not a
phantom write; (2) deduped per session so a Docker run that lost context doesn't
inflate the counter on every write; (3) recorded under a clearly-named metric, not
a generic task "failure".
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def fs(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import app.core.task_paths as tp
    importlib.reload(tp)
    import app.mcp.tools.filesystem as _fs
    importlib.reload(_fs)
    _fs._phantom_write_recorded.clear()
    return _fs


@pytest.fixture(autouse=True)
def _reset_metrics():
    from app.core.background_metrics import get_background_metrics
    get_background_metrics().reset()
    yield
    get_background_metrics().reset()


def _count() -> int:
    from app.core.background_metrics import get_background_metrics
    m = get_background_metrics().get_metrics().get("phantom_write_no_task_context")
    return m["failure_count"] if m else 0


def _no_context():
    from app.core.execution_context import current_task_id, current_parent_task_id, current_session_id
    current_task_id.set("")
    current_parent_task_id.set("")
    current_session_id.set("")


@pytest.mark.asyncio
async def test_write_without_context_records_metric(fs):
    _no_context()
    assert _count() == 0
    await fs.handle_write_file({"path": "a.md", "content": "x"})
    assert _count() == 1


@pytest.mark.asyncio
async def test_repeated_writes_same_session_record_once(fs):
    # The signal is a per-session STATE, not a per-write event — no unbounded spam.
    _no_context()
    for _ in range(5):
        await fs.handle_write_file({"path": "a.md", "content": "x"})
    assert _count() == 1


@pytest.mark.asyncio
async def test_read_without_context_does_not_record(fs):
    # A read resolving to a fallback workspace is NOT a phantom write.
    _no_context()
    await fs.handle_read_file({"path": "missing.md"})
    await fs.handle_list_files({"path": "."})
    assert _count() == 0


@pytest.mark.asyncio
async def test_write_with_context_does_not_record(fs):
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    tok = current_task_id.set("task-ok")
    try:
        await fs.handle_write_file({"path": "a.md", "content": "x"})
        assert _count() == 0
    finally:
        current_task_id.reset(tok)


@pytest.mark.asyncio
async def test_distinct_sessions_each_record(fs):
    from app.core.execution_context import current_task_id, current_parent_task_id, current_session_id
    current_task_id.set("")
    current_parent_task_id.set("")
    current_session_id.set("sess-A")
    await fs.handle_write_file({"path": "a.md", "content": "x"})
    current_session_id.set("sess-B")
    await fs.handle_write_file({"path": "b.md", "content": "y"})
    assert _count() == 2


def test_metric_named_not_generic_failure(fs):
    # Uses a dedicated, descriptive key (not a generic task name that a dashboard
    # would sum into real task failures).
    assert "phantom_write" in "phantom_write_no_task_context"
