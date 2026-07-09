"""Resuming a cancelled task must clear the durable cancel flag.

Root cause (found in final whole-branch review): /tasks/{id}/resume reset the
task status to "executing" but never removed metadata.cancel_requested. Because
Task 6 added a DB-flag backstop (pipeline._is_cancelled_db reads cancel_requested
at every turn boundary), a resumed cancelled task would re-cancel itself at turn 1
— making resume-after-cancel impossible.

clear_cancel_flags() is the pure helper the resume endpoint uses to strip the
cancel flags while preserving the rest of the metadata. We test it standalone
(no DB) the same way the other cancel_signal helpers are tested.
"""

from __future__ import annotations

from app.core.cancel_signal import clear_cancel_flags


def test_clear_removes_both_cancel_keys():
    meta = {"cancel_requested": True, "cancel_requested_at": "2026-06-18T00:00:00Z"}
    out = clear_cancel_flags(meta)
    assert "cancel_requested" not in out
    assert "cancel_requested_at" not in out


def test_clear_preserves_other_metadata():
    meta = {"cancel_requested": True, "resume_count": 2, "resumed": True}
    out = clear_cancel_flags(meta)
    assert out["resume_count"] == 2
    assert out["resumed"] is True
    assert "cancel_requested" not in out


def test_clear_returns_new_dict_does_not_mutate_input():
    # The endpoint relies on reassigning a NEW dict so SQLAlchemy flags the JSON
    # column dirty; clear_cancel_flags must therefore not mutate its argument.
    meta = {"cancel_requested": True, "x": 1}
    out = clear_cancel_flags(meta)
    assert out is not meta
    assert meta["cancel_requested"] is True  # original untouched


def test_clear_handles_empty_and_missing_flags():
    assert clear_cancel_flags({}) == {}
    assert clear_cancel_flags({"x": 1}) == {"x": 1}
