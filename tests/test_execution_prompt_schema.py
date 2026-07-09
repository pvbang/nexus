"""build_agent_execution_prompt must teach the ACCOMPLISHED/KEY FINDINGS/STILL
MISSING output schema (G1, prompt audit 2026-07-01).

The orchestrator extracts did_what by preferring the ## ACCOMPLISHED section
(ledger_extract.extract_did_what, after the tri-model context fix). If the
execution prompt never asks the agent to emit that schema, the section only
appears when a separate self-assessment LLM-call succeeds — and when it doesn't,
extraction falls back to the tail heuristic the context work aimed to avoid. So
the schema must be taught in the execution prompt itself."""

from __future__ import annotations

from app.orchestration.prompts import build_agent_execution_prompt


def _prompt() -> str:
    return build_agent_execution_prompt(
        agent_name="Eng",
        agent_role="engineer",
        personality="",
        expertise=["mechanical"],
        thinking_style="analytical",
        assignment="Design the reactor",
        success_criteria="Sizing closes <5%",
        shared_context="",
        own_thinking="",
        previous_turns="",
        tool_catalog="",
        workspace_path="workspace_data/tasks/x/_work",
    )


def test_execution_prompt_teaches_output_schema():
    p = _prompt()
    assert "## ACCOMPLISHED" in p
    assert "## KEY FINDINGS" in p
    assert "## STILL MISSING" in p


def test_execution_prompt_teaches_utf8_portability():
    # Scripts that print/write Vietnamese/emoji crashed with UnicodeEncodeError on
    # non-UTF-8 stdout (verify scripts across the gasifier reruns) — the coding
    # standards must warn agents to be encoding-safe (audit rerun 2026-07-01).
    p = _prompt()
    assert "Text portability" in p
    assert "utf-8" in p.lower()
    assert "UnicodeEncodeError" in p
