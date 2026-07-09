"""Ground-truth `produced_deliverable` signal on AgentResult (E3).

Runs 68c276ac and 1894f0ef both logged turns as ``success=True`` while the agent
dispatched ZERO real tool calls — just long prose. The phantom-write doctrine
deliberately keeps a prose-only analysis/wrap-up turn as success (don't kill
legitimate analysis), so `success` alone cannot tell "did work reach disk?" from
"talked about work." The decision/loop layer needs a SEPARATE ground-truth signal.

`_did_produce_deliverable(tool_calls)` is that signal: True iff at least one
PRODUCE tool (write_file / edit_file / execute_code / execute_command / ...) ran
SUCCESSFULLY this turn. Read/analysis tools and prose do not count. It never
alters `success`; it is a distinct field the loop gate (E7) consumes to detect
"N turns of talk, nothing produced."
"""

from __future__ import annotations

from app.orchestration.agent_worker import AgentWorker
from app.orchestration.models import ToolCall


def test_successful_write_file_counts_as_produced():
    tcs = [ToolCall(tool_name="write_file", output="wrote r.md", success=True)]
    assert AgentWorker._did_produce_deliverable(tcs) is True


def test_execute_code_success_counts_as_produced():
    tcs = [ToolCall(tool_name="execute_code", output="ran", success=True)]
    assert AgentWorker._did_produce_deliverable(tcs) is True


def test_failed_produce_tool_does_not_count():
    tcs = [ToolCall(tool_name="write_file", output="error", success=False)]
    assert AgentWorker._did_produce_deliverable(tcs) is False


def test_read_only_tools_do_not_count_as_produced():
    tcs = [
        ToolCall(tool_name="read_file", output="contents", success=True),
        ToolCall(tool_name="list_files", output="a\nb", success=True),
    ]
    assert AgentWorker._did_produce_deliverable(tcs) is False


def test_no_tool_calls_is_not_produced():
    # The prose-only turn: zero tools. NOT a produce (but may still be a valid
    # analysis turn — success is decided separately).
    assert AgentWorker._did_produce_deliverable([]) is False


def test_mixed_read_and_successful_write_counts():
    tcs = [
        ToolCall(tool_name="read_file", output="x", success=True),
        ToolCall(tool_name="write_file", output="ok", success=True),
    ]
    assert AgentWorker._did_produce_deliverable(tcs) is True
