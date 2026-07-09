"""Tests for cross-process cancel signal helpers + resumable-state policy.

These are pure functions (no Redis / no DB) so they pin the contract that
the cancel endpoint, the pub/sub subscriber, and the resume list all share.
"""

from __future__ import annotations

from app.core.cancel_signal import (
    CANCEL_CHANNEL,
    RESUMABLE_STATES,
    build_cancel_payload,
    parse_cancel_payload,
    should_keep_cancelled,
)


def test_build_and_parse_roundtrip():
    payload = build_cancel_payload("task-123", "sess-abc")
    assert payload == {"task_id": "task-123", "session_id": "sess-abc"}
    assert parse_cancel_payload(payload) == ("task-123", "sess-abc")


def test_parse_missing_task_id_returns_none_pair():
    assert parse_cancel_payload({"session_id": "x"}) == (None, None)
    assert parse_cancel_payload({}) == (None, None)


def test_channel_constant():
    assert CANCEL_CHANNEL == "nexus:cancel"


def test_resumable_states_cover_cancel_and_crash():
    # The resume LIST and the /resume ENDPOINT must agree on these states.
    for s in ("cancelled", "failed", "error", "crashed", "suspended"):
        assert s in RESUMABLE_STATES


def test_should_keep_cancelled_when_cancel_requested():
    # Task finished its loop normally but a cancel was requested mid-flight —
    # the terminal writer must NOT overwrite to "completed".
    assert should_keep_cancelled("completed", cancel_requested=True) is True


def test_should_keep_cancelled_when_already_cancelled():
    assert should_keep_cancelled("cancelled", cancel_requested=False) is True


def test_should_not_keep_when_clean_completion():
    assert should_keep_cancelled("completed", cancel_requested=False) is False
