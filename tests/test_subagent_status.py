"""Subagent status values are a validated, single-source-of-truth contract."""

from app.services.subagent_status import SubagentStatus


def test_known_values_present():
    for s in ("pending", "completed", "failed", "rejected",
              "error", "no_executor", "cancelled", "timed_out"):
        assert s in SubagentStatus.VALID
        assert SubagentStatus.is_valid(s)


def test_unknown_is_invalid():
    assert not SubagentStatus.is_valid("complete")  # common typo
    assert not SubagentStatus.is_valid("done")


def test_terminal_excludes_pending():
    assert "pending" not in SubagentStatus.TERMINAL
    assert "completed" in SubagentStatus.TERMINAL
    assert "failed" in SubagentStatus.TERMINAL


def test_constants_match_strings():
    assert SubagentStatus.COMPLETED == "completed"
    assert SubagentStatus.NO_EXECUTOR == "no_executor"


def test_delegation_result_coerces_invalid_status():
    from app.services.subagent_delegator import DelegationResult
    r = DelegationResult(task_index=0, goal="x", status="bogus_status")
    assert r.status == SubagentStatus.ERROR  # coerced, not silently kept


def test_delegation_result_keeps_valid_status():
    from app.services.subagent_delegator import DelegationResult
    r = DelegationResult(task_index=0, goal="x", status="completed")
    assert r.status == "completed"
