"""Surface a SILENT tool-call parse-drop instead of treating it as 'done' (P0-2).

Round-2 audit (task-03ce93bd): a model emitted ~30 <tool_call> blocks the parser
couldn't read; each turn ended with tool_calls=[] and the drop was INVISIBLE
(tools_used=[]), so the orchestrator + Evolution spent 6 turns misdiagnosing it as
a shell/env/write_file failure. When content carries tool-call MARKUP but zero
calls were parsed OR recovered, that is a parse-drop, not a final answer — it must
be surfaced (logged + fed back to the agent to re-emit in a clean format).
"""

from __future__ import annotations

from app.orchestration.agent_worker import content_has_unparsed_toolcall_markup


def test_detects_tool_call_tag_markup():
    assert content_has_unparsed_toolcall_markup(
        "<tool_call>list_files\n<arg_key>path</arg_key>\n</tool_call>"
    ) is True


def test_detects_arg_key_markup():
    assert content_has_unparsed_toolcall_markup("blah <arg_key>command</arg_key> blah") is True


def test_detects_fullwidth_pipe_toolcall_markup():
    assert content_has_unparsed_toolcall_markup("<｜tool▁call▁begin｜>list_files") is True


def test_detects_invoke_markup():
    assert content_has_unparsed_toolcall_markup("<invoke name=\"list_files\">") is True


def test_plain_prose_is_not_flagged():
    assert content_has_unparsed_toolcall_markup(
        "Phase 1 reconnaissance complete. Found 17 subdomains and reported them."
    ) is False


def test_prose_mentioning_the_word_toolcall_is_not_flagged():
    # Merely discussing tool calls in prose (no actual tag) is not a parse-drop.
    assert content_has_unparsed_toolcall_markup(
        "The agent should make a tool call to list_files next."
    ) is False


def test_empty_is_not_flagged():
    assert content_has_unparsed_toolcall_markup("") is False
    assert content_has_unparsed_toolcall_markup(None) is False
