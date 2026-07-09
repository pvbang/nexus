"""Honest turn success for no-op turns (task 3c44a93f bug #6a).

AgentWorker computed success as ``not _agent_failed and not _self_reflection
and not _force_stopped and not _phantom_produce`` — an inversion-of-default
("no evidence of a KNOWN failure ⇒ success"). A turn that dispatched ZERO
tools AND produced empty/whitespace output (Turns 6/8/10 in task 3c44a93f)
matched none of those flags, so it was logged ``success=true, output_chars=0``.
That false success fooled the decision layer into continuing to dispatch.

The fix adds a no-op detector folded into the success computation:
- 0 tool calls AND empty/whitespace output ⇒ NOT success.
- A turn with ANY real output (even a short tools-off wrap-up) and 0 tools is
  STILL success — the deliberate distinction the phantom-write audit insisted
  on (don't kill legitimate analysis/wrap-up turns). The gate is "absence of
  BOTH signals", never a turn counter or length heuristic.
"""

from __future__ import annotations

from app.orchestration.agent_worker import AgentWorker
from app.orchestration.models import ToolCall


def test_no_op_turn_zero_tools_empty_output_is_no_op():
    assert AgentWorker._is_no_op_turn([], "") is True


def test_no_op_turn_whitespace_only_output_is_no_op():
    assert AgentWorker._is_no_op_turn([], "   \n\t  ") is True


def test_turn_with_a_tool_call_is_not_no_op():
    tc = [ToolCall(tool_name="dns_lookup", output="1.2.3.4", success=True)]
    assert AgentWorker._is_no_op_turn(tc, "") is False


def test_turn_with_real_analysis_prose_is_not_no_op():
    """A substantive analysis/critic turn with no tools must NOT be a no-op —
    protects the phantom-analysis turns the prior audit refused to kill."""
    prose = (
        "After reviewing the recon output I conclude the SPF record exposes the "
        "mail provider and the crt.sh subdomains warrant Phase 2 enumeration."
    )
    assert AgentWorker._is_no_op_turn([], prose) is False


def test_short_tools_off_wrapup_is_not_no_op():
    """A short tools-off wrap-up with ANY real content stays NOT a no-op —
    the phantom-write audit deliberately keeps these as success."""
    assert AgentWorker._is_no_op_turn([], "Report finished.") is False
