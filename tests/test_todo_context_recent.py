"""When TODO.md exceeds the context cap, the injected slice must keep the MOST
RECENT status (tail), not the stale plan head — else the agent can't see what's
just been completed and repeats it (audit tri-model 2026-07-01, N1)."""

from __future__ import annotations

from app.orchestration.turn_executor import _todo_context_slice


def test_todo_slice_keeps_recent_status_over_stale_head():
    todo = (
        "# Task TODO\n## Execution Plan\n"
        + ("### Turn 1: PHASE 1 — recon\n- Pending\n" * 200)  # long stale head
        + "\n## Current Status — Turn 9 | Progress: 40%\n"
        "### Completed\n- [x] Built the model this turn\n"
        "### Next Focus\nRun the validation suite.\n"
    )
    out = _todo_context_slice(todo, max_chars=3000)
    assert "Current Status — Turn 9" in out
    assert "Built the model this turn" in out
    assert "Run the validation suite" in out


def test_todo_slice_unchanged_when_small():
    todo = "# Task TODO\n## Current Status — Turn 1 | Progress: 10%\n"
    assert _todo_context_slice(todo, max_chars=3000) == todo
