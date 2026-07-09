"""Cross-turn hard-skip fires ONLY for exhausted failures (E-R1/E-R2).

Root cause E-R1/E-R2 (tri-run audit 2026-07-02, task-4b271111 DeepSeek). The
CrossTurnRegistry recorded that an action had failed repeatedly, but should_skip()
was dead code — no caller — so a weak model looped the SAME failing call across
turns (list_files on a missing dir reached attempt_count=10). agent_worker now
consults should_skip before executing a tool and hard-skips an action that already
FAILED 3+ times.

Critically it must NOT hard-skip an action that previously SUCCEEDED — re-reading a
file to continue or re-running a fixed script is legitimate work. These tests pin
that asymmetry on the registry predicate the worker gate relies on.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKSPACE_DIR", "workspace_data")
    import app.core.task_paths as tp
    importlib.reload(tp)
    tp._parent_cache.clear()
    from app.orchestration.cross_turn_registry import CrossTurnRegistry
    from app.core.execution_context import current_task_id, current_parent_task_id
    current_parent_task_id.set("")
    tok = current_task_id.set("skiptk01")
    yield CrossTurnRegistry("skiptk01")
    current_task_id.reset(tok)


def test_exhausted_failure_is_skipped_with_failed_reason(registry):
    key = "list_files:{'path': 'workspace_data/recon'}"
    for turn in (1, 2, 3):
        registry.register_completed(key, "recon", turn, success=False,
                                    outcome_summary="Directory not found")
    skip, reason = registry.should_skip(key)
    assert skip is True
    # The worker gate keys on the word "Failed" — pin it.
    assert "Failed" in reason


def test_prior_success_is_not_a_failed_skip(registry):
    """A succeeded action may be 'skip' in the registry's own semantics, but its
    reason is NOT a failure — the worker gate must leave it runnable (re-reads /
    re-runs are legitimate)."""
    key = "read_file:{'path': 'report.md'}"
    registry.register_completed(key, "writer", 1, success=True,
                                outcome_summary="read 200 lines")
    skip, reason = registry.should_skip(key)
    # Whatever the skip flag, it must NOT be a Failed-reason (that's the only branch
    # the worker hard-blocks on).
    assert "Failed" not in reason


def test_one_or_two_failures_still_allow_retry(registry):
    key = "http_request:{'url': 'https://crt.sh'}"
    for turn in (1, 2):
        registry.register_completed(key, "recon", turn, success=False,
                                    outcome_summary="timeout")
    skip, reason = registry.should_skip(key)
    assert skip is False  # retry still allowed under 3 attempts
    assert "Failed" not in reason


def test_unseen_action_is_not_skipped(registry):
    skip, reason = registry.should_skip("nmap:{'target': 'x'}")
    assert skip is False
    assert reason == ""
