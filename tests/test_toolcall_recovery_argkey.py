"""Recover the ``<tool_call>NAME <arg_key>/<arg_value>`` template (R2-F).

Root cause R2-F (tri-run audit round-2, 2026-07-03, task-03ce93bd). A weak model
served this run emitted tool calls in a DeepSeek/GLM-style template where the tool
NAME is a bare token right after ``<tool_call>`` and the arguments are
``<arg_key>k</arg_key><arg_value>v</arg_value>`` pairs — NOT a JSON object with a
``name`` field, and NOT the Anthropic ``<invoke name=..><parameter..>`` spelling.

``recover_tool_calls`` had only two branches — delimiter-adjacent JSON (which needs
``name`` INSIDE the JSON) and ``<invoke>`` XML — so it recovered NOTHING here. ~30
raw ``<tool_call>`` blocks → 0 executed → the task sat at 0% for 6 turns, with the
parse-drop invisible (``tools_used=[]``).

This adds a third recovery branch for the ``<tool_call>`` + name-token +
``<arg_key>/<arg_value>`` template. Best-effort: a well-formed pair yields an
argument; a malformed/valueless key is skipped rather than crashing the parse.
"""

from __future__ import annotations

from app.services.toolcall_recovery import recover_tool_calls

_VALID = {"list_files", "execute_command", "write_file", "web_search", "read_file"}


def test_recovers_toolcall_name_with_argkey_value_pairs():
    content = (
        "Tôi sẽ chạy lệnh.\n"
        "<tool_call>execute_command\n"
        "<arg_key>command</arg_key>\n"
        "<arg_value>whois pln.co.id</arg_value>\n"
        "</tool_call>"
    )
    calls = recover_tool_calls(content, _VALID)
    assert len(calls) == 1, f"expected 1 recovered call, got {calls}"
    assert calls[0]["function"]["name"] == "execute_command"
    import json
    args = json.loads(calls[0]["function"]["arguments"])
    assert args.get("command") == "whois pln.co.id"


def test_recovers_bare_name_even_when_args_are_malformed():
    """The exact list_files shape from the run: name recovered, malformed/valueless
    keys skipped (no crash). Recovering the NAME alone still un-sticks the agent."""
    content = (
        "<tool_call>list_files\n"
        "<arg_key>path</arg_key>\n"
        "<arg_key>recursive</arg_key>\n"
        "<arg_key>true</arg_key>\n"
        "</tool_call>"
    )
    calls = recover_tool_calls(content, _VALID)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "list_files"


def test_only_recovers_valid_tool_names():
    content = "<tool_call>not_a_real_tool\n<arg_key>x</arg_key><arg_value>1</arg_value>\n</tool_call>"
    assert recover_tool_calls(content, _VALID) == []


def test_multiple_toolcall_blocks_all_recovered():
    content = (
        "<tool_call>list_files\n<arg_key>path</arg_key><arg_value>.</arg_value>\n</tool_call>\n"
        "then\n"
        "<tool_call>web_search\n<arg_key>query</arg_key><arg_value>pln.co.id whois</arg_value>\n</tool_call>"
    )
    calls = recover_tool_calls(content, _VALID)
    names = sorted(c["function"]["name"] for c in calls)
    assert names == ["list_files", "web_search"], f"got {names}"


def test_prose_mentioning_tool_call_word_is_not_recovered():
    """Guard: prose that merely says 'tool_call' with no <tool_call> tag + valid
    name must not fabricate a call."""
    content = "The agent should emit a tool_call to list_files but did not."
    assert recover_tool_calls(content, _VALID) == []


def test_existing_json_and_invoke_paths_still_work():
    """Regression: the two original recovery branches must be unaffected."""
    import json
    # JSON-adjacent-to-delimiter (name INSIDE json)
    j = '<tool_call>{"name": "list_files", "arguments": {"path": "."}}</tool_call>'
    calls = recover_tool_calls(j, _VALID)
    assert any(c["function"]["name"] == "list_files" for c in calls)
