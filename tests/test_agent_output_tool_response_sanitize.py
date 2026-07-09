"""Persist-side sanitize of fabricated <tool_response> blocks (E8, run 3a75a0db).

`strip_tool_call_markup` cleans the DISPLAY string (agent_thinking event), but the
agent's `final_content` is ALSO persisted as `AgentResult.output` and flows into
downstream context: pipeline synthesis (`**{agent}**: {output}`), `_extract_did_what`,
and turn_executor fact collection. A model that fabricates a `<tool_response>`
("File written: x.json (3.38 KB) ✅") therefore plants a self-authored lie that
later turns read as ground truth — the phantom-write spreads across turns.

`_sanitize_output_for_context` removes ONLY the fabricated `<tool_response>` blocks
(machinery the system never emits into a model's own content). It deliberately does
NOT run the full display strip: `output` may legitimately contain deliverable prose
with pipe characters or fenced code, and the display strip truncates at the first
pipe token — safe for a 1000-char reasoning preview, lossy for full persisted output.
"""

from __future__ import annotations

from app.orchestration.agent_worker import _sanitize_output_for_context as s


def test_fabricated_tool_response_removed_from_output():
    text = (
        "I'll write the design basis file now.\n"
        "<tool_response>\nFile written: x.json (3.38 KB)\n</tool_response>\n"
        "The file is confirmed written and verified."
    )
    out = s(text)
    assert "I'll write the design basis file now." in out
    assert "The file is confirmed written and verified." in out
    assert "tool_response" not in out
    assert "File written" not in out


def test_pipe_and_fence_prose_preserved_in_output():
    # Unlike the display strip, persist-side sanitize must NOT truncate at a pipe
    # or gut a fenced code block — this is real deliverable content.
    text = (
        "Analysis: throughput is a|b ratio.\n"
        "```python\nx = compute(a, b)\n```\n"
        "Conclusion: design closes within 5%."
    )
    out = s(text)
    assert "a|b ratio" in out
    assert "compute(a, b)" in out
    assert "Conclusion: design closes within 5%." in out


def test_plain_output_untouched():
    text = "Reactor throat diameter is 108mm; hearth load per Reed & Das."
    assert s(text) == text


def test_none_and_empty_safe():
    assert s(None) is None
    assert s("") == ""
