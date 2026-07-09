"""Tests for the phantom-write fix (native-provider tool-call dropped in prose).

Root cause (audit 2026-06-25): on native function-calling providers (bedrock /
claude-opus), the model sometimes emits a tool call as a JSON blob INSIDE its
prose `content` while leaving the native `tool_calls` slot empty. The recovery
parser `parse_tool_calls_from_text` exists but is gated to prompted providers in
`llm_client`, so it never runs for bedrock. The worker then treats the prose as a
final answer, breaks, and reports `success=True` with `tools_used=[]` — nothing
reaches disk ("phantom write").

F1: when a turn returns content but no native tool_calls, the WORKER itself
    recovers tool-call-shaped JSON from the content and executes it.
F2: an agent that demonstrably TRIED to produce a deliverable (emitted a
    produce-tool call in prose) but landed zero successful produce-tool calls
    must report success=False — never a phantom "success".

These exercise the real `AgentWorker.execute` loop with a fake LLM that mimics the
bedrock failure mode (content carries the JSON, native tool_calls is empty).
"""

from __future__ import annotations

import json

from app.orchestration.agent_worker import AgentWorker
from app.orchestration.models import AgentAssignment


# A write_file tool schema in registry format (function-wrapped, like the live one).
_WRITE_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file in the workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
}


class _PhantomThenDoneLLM:
    """Fake native-provider LLM reproducing the phantom-write failure mode.

    Round 0: returns the write_file call as a JSON blob inside `content`, with an
             EMPTY native `tool_calls` slot (exactly what bedrock did at T18).
    Round 1+ (after the tool result comes back): returns a plain final answer with
             no tool calls — the agent is done.

    Wrap-up / synthesis calls (no `tools=`) always return plain text.
    """

    def __init__(self, path="report/BAO_CAO.md", content="# Final report\nbody"):
        self.calls = 0
        self.tool_offered_calls = 0
        self._blob = json.dumps(
            {"name": "write_file", "arguments": {"path": path, "content": content}},
            ensure_ascii=False,
        )

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if not tools:
            # Wrap-up / synthesis / assessment calls — plain text only.
            return {"content": "Report finished.", "tool_calls": []}
        self.tool_offered_calls += 1
        if self.tool_offered_calls == 1:
            # The phantom: tool call lives in prose, native slot empty.
            return {
                "content": (
                    "I'll write the consolidated report now.\n\n"
                    f"{self._blob}\n"
                    "</tool_call>\n"
                ),
                "tool_calls": [],
            }
        # Subsequent rounds: agent concludes (no tool calls).
        return {"content": "The report has been written. Done.", "tool_calls": []}


class _PureProseLLM:
    """Fake LLM that only ever produces analysis prose — no tool calls, no JSON.

    Represents a legitimate analysis turn (e.g. a critic reviewing). It must NOT
    be retroactively failed by F2 just because it produced no files.
    """

    async def chat(self, messages, tools=None, **kwargs):
        return {
            "content": (
                "## Analysis\nThe 1km requirement is infeasible for sigma=0.005 "
                "because N_req (44,304) far exceeds N_phys_max (2,764). This is a "
                "robust conclusion; no further parameter sweep is warranted."
            ),
            "tool_calls": [],
        }


class _RecordingExecutor:
    """Tool executor that records every call and reports success."""

    def __init__(self, succeed=True):
        self.calls: list[tuple[str, dict]] = []
        self._succeed = succeed

    async def execute(self, tool_name, tool_args):
        self.calls.append((tool_name, tool_args))
        if self._succeed:
            return {"success": True, "output": f"wrote {tool_args.get('path', '?')}"}
        return {"success": False, "output": "disk error: permission denied"}


def _worker(llm, executor):
    return AgentWorker(
        agent_name="Technical Report Writer",
        agent_role="writer",
        llm=llm,
        tool_executor=executor,
        tool_schemas=[_WRITE_FILE_SCHEMA],
    )


def _assignment():
    return AgentAssignment(
        agent_role="writer",
        agent_name="Technical Report Writer",
        task_description="Write the consolidated technical report to disk.",
        success_criteria="A report file exists in the workspace.",
        tools_hint=["write_file"],
    )


# ─────────────────────────────── F1 ───────────────────────────────


async def test_worker_recovers_and_executes_tool_call_from_prose():
    """F1: a write_file call emitted as prose-JSON must actually be executed."""
    llm = _PhantomThenDoneLLM(path="report/BAO_CAO.md")
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    # The phantom tool call was recovered and dispatched to the executor.
    assert any(name == "write_file" for name, _ in ex.calls), (
        "write_file emitted in prose was never executed (phantom write persisted)"
    )
    wrote = next(args for name, args in ex.calls if name == "write_file")
    assert wrote.get("path") == "report/BAO_CAO.md"
    # A real produce-tool ran successfully → the turn is a genuine success.
    assert result.success is True
    assert any(tc.tool_name == "write_file" and tc.success for tc in result.tool_calls)


# ─────────────────────────────── F2 ───────────────────────────────


async def test_phantom_write_when_execution_fails_is_not_success():
    """F2: tried to produce (recovered call) but the write FAILED → success=False."""
    llm = _PhantomThenDoneLLM()
    ex = _RecordingExecutor(succeed=False)  # the write_file call fails on disk
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    # It did attempt the write (recovered from prose) but no produce succeeded.
    assert any(name == "write_file" for name, _ in ex.calls)
    assert result.success is False, (
        "agent tried to produce a deliverable, every produce-tool failed, "
        "yet success was reported True (phantom success)"
    )


async def test_pure_analysis_turn_is_not_failed_by_f2():
    """F2 guard: a legitimate analysis turn (no produce intent) stays success=True."""
    llm = _PureProseLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = AgentWorker(
        agent_name="Quantitative Design Critic",
        agent_role="critic",
        llm=llm,
        tool_executor=ex,
        tool_schemas=[_WRITE_FILE_SCHEMA],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="critic",
            agent_name="Quantitative Design Critic",
            task_description="Review the link-budget reasoning for errors.",
            success_criteria="A critique is produced.",
        ),
        shared_context="",
    )

    assert not ex.calls, "analysis turn unexpectedly executed a tool"
    assert result.success is True, "a pure-analysis turn was wrongly failed by F2"


async def test_phantom_failure_carries_a_reason():
    """F2: a phantom-produce turn surfaces a human-readable blocker reason."""
    llm = _PhantomThenDoneLLM()
    ex = _RecordingExecutor(succeed=False)
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert result.success is False
    assert "produce" in result.error.lower() and result.error, (
        "phantom failure must carry a reason for the LEDGER blockers field"
    )


class _NeverRecoversLLM:
    """Native LLM whose FINAL answer is a produce-tool JSON it never executed.

    Reproduces phantom source #2: a tools-off wrap-up that *describes* a write_file
    in prose. The worker's recovery only runs when tools were offered, so this blob
    survives into final_content — F2 Signal B must still catch it as phantom.
    """

    def __init__(self):
        self.round = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.round += 1
        if not tools:
            # Wrap-up call (tools stripped): a genuine call attempt leaked to prose
            # (carries the </tool_call> delimiter the model's machinery emitted).
            return {
                "content": (
                    "I'll write the final output now.\n"
                    '{"name": "write_file", "arguments": '
                    '{"path": "out.md", "content": "x"}}\n'
                    "</tool_call>"
                ),
                "tool_calls": [],
            }
        # First tooled round: a normal failing tool call so the loop continues a bit.
        return {"content": "", "tool_calls": []}


async def test_wrapup_with_plain_prose_is_not_false_positive():
    """F2 guard: a tools-off wrap-up that returns plain prose stays success=True."""
    llm = _PhantomThenDoneLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)
    # Force the wrap-up (tools-off) branch immediately.
    worker._max_rounds = 0

    result = await worker.execute(assignment=_assignment(), shared_context="")

    # max_rounds=0 → straight to tools-off wrap-up; _PhantomThenDoneLLM returns
    # plain prose "Report finished." → no produce intent → NOT phantom.
    assert result.success is True


async def test_signal_b_catches_unexecuted_blob_in_final_content():
    """F2 Signal B (direct): final_content carrying a produce-blob fails the turn."""
    llm = _NeverRecoversLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)
    worker._max_rounds = 0  # go straight to tools-off wrap-up

    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert not ex.calls, "no tool should have executed"
    assert result.success is False, (
        "final answer was an unexecuted write_file call must not be success"
    )


# ============ false-positive guards (reviewer-found defect) ============
# A deliverable may legitimately DOCUMENT a produce-tool call as a JSON example
# (e.g. an agent-design report, an API doc). Without an explicit tool-call
# delimiter, that example must never be (F1) executed nor (F2) treated as a
# phantom produce. The genuine bedrock leak always carries a delimiter the
# model's tool-calling machinery emitted (</tool_call>, ```tool_call, <invoke).

# A report body that quotes a write_file call inside a ```json fence as an EXAMPLE.
_DOC_REPORT = (
    "## Agent design\n"
    "The writer agent should persist its report. For example, it emits:\n"
    "```json\n"
    '{"name": "write_file", "arguments": {"path": "report.md", "content": "..."}}\n'
    "```\n"
    "This is illustrative only; the orchestrator routes the real call.\n"
)


class _DocumentsExampleLLM:
    """Native LLM returning an analysis report that quotes a write_file EXAMPLE.

    The JSON is documentation (fenced ```json, no tool-call delimiter), NOT an
    actionable call. It must not be executed (F1) or failed (F2).
    """

    async def chat(self, messages, tools=None, **kwargs):
        return {"content": _DOC_REPORT, "tool_calls": []}


class _ToolUseDelimiterLLM:
    """Native Claude/bedrock leak spelled with <tool_use> (Claude's own block name).

    The Anthropic API returns tool calls as content blocks of type "tool_use", so a
    leaked call most plausibly reflects that name. The worker must recover it.
    """

    def __init__(self):
        self.tool_offered = 0

    async def chat(self, messages, tools=None, **kwargs):
        if not tools:
            return {"content": "Done.", "tool_calls": []}
        self.tool_offered += 1
        if self.tool_offered == 1:
            return {
                "content": (
                    "<tool_use>\n"
                    '{"name": "write_file", "arguments": '
                    '{"path": "report/out.md", "content": "body"}}\n'
                    "</tool_use>"
                ),
                "tool_calls": [],
            }
        return {"content": "The report is written. Done.", "tool_calls": []}


async def test_tool_use_delimited_leak_is_recovered():
    """F1: a genuine leak spelled with <tool_use> (Claude native block) is executed."""
    llm = _ToolUseDelimiterLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert any(name == "write_file" for name, _ in ex.calls), (
        "a <tool_use>-delimited genuine leak was dropped (phantom write persisted)"
    )
    assert result.success is True


async def test_documented_example_is_not_executed():
    """F1 guard: a fenced ```json write_file EXAMPLE (no delimiter) is not executed."""
    llm = _DocumentsExampleLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert not ex.calls, (
        "a documented write_file example was wrongly executed as a real tool call"
    )


async def test_report_quoting_example_is_not_failed_by_f2():
    """F2 guard: an analysis turn quoting a write_file example stays success=True."""
    llm = _DocumentsExampleLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = AgentWorker(
        agent_name="Agent Architect",
        agent_role="researcher",
        llm=llm,
        tool_executor=ex,
        tool_schemas=[_WRITE_FILE_SCHEMA],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="researcher",
            agent_name="Agent Architect",
            task_description="Document how the writer agent should call write_file.",
            success_criteria="A design note exists.",
        ),
        shared_context="",
    )

    assert not ex.calls
    assert result.success is True, (
        "a report quoting a write_file example was wrongly marked a phantom failure"
    )


# A deliverable that EXPLAINS the tool-calling protocol: it mentions the literal
# </tool_call> delimiter in a PROSE sentence, and separately shows a write_file
# JSON inside a ```json fence. The delimiter is NOT adjacent to the JSON — a pure
# substring check would wrongly fire here (the reviewer-found residual). A
# structural-adjacency gate must reject it.
_PROTOCOL_DOC = (
    "## How tool-calling works\n"
    "A model signals a call by ending its turn with the </tool_call> marker.\n"
    "For example, to persist a file it would emit this JSON object:\n"
    "```json\n"
    '{"name": "write_file", "arguments": {"path": "out.md", "content": "..."}}\n'
    "```\n"
    "This section is documentation only; nothing here should run.\n"
)


class _ExplainsProtocolLLM:
    """Returns a deliverable that documents the protocol (delimiter in prose, JSON
    example in a separate ```json fence). Must not be executed (F1) or failed (F2).
    """

    async def chat(self, messages, tools=None, **kwargs):
        return {"content": _PROTOCOL_DOC, "tool_calls": []}


async def test_protocol_doc_delimiter_in_prose_is_not_executed():
    """F1 guard: a delimiter mentioned in PROSE (not adjacent to the JSON) is inert."""
    llm = _ExplainsProtocolLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert not ex.calls, (
        "a delimiter quoted in prose, far from the JSON example, was wrongly "
        "treated as a real tool call and executed"
    )


async def test_protocol_doc_is_not_failed_by_f2():
    """F2 guard: documenting the protocol stays success=True (no produce intent)."""
    llm = _ExplainsProtocolLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = AgentWorker(
        agent_name="Protocol Author",
        agent_role="researcher",
        llm=llm,
        tool_executor=ex,
        tool_schemas=[_WRITE_FILE_SCHEMA],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="researcher",
            agent_name="Protocol Author",
            task_description="Explain how agents emit a tool call and end with </tool_call>.",
            success_criteria="A protocol note exists.",
        ),
        shared_context="",
    )

    assert not ex.calls
    assert result.success is True, (
        "a protocol-documentation turn was wrongly marked a phantom failure"
    )


# edit_file is the canonical file-modification tool (sibling of write_file). An
# agent assigned to MODIFY a deliverable may use only edit_file, so the F2 guard
# must treat a failed edit_file as a phantom produce too.
_EDIT_FILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "edit_file",
        "description": "Edit an existing file in the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}


class _NativeEditFailsLLM:
    """Native tool call to edit_file that fails (no prose-recovery involved)."""

    def __init__(self):
        self.tool_offered = 0

    async def chat(self, messages, tools=None, **kwargs):
        if not tools:
            return {"content": "Edited.", "tool_calls": []}
        self.tool_offered += 1
        if self.tool_offered == 1:
            return {
                "content": "Editing the file.",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "edit_file", "arguments": "{\"path\": \"r.md\"}"}}
                ],
            }
        return {"content": "Done.", "tool_calls": []}


async def test_failed_edit_file_is_phantom_produce():
    """F2: edit_file is a produce-tool — a failed edit with no success is not success."""
    llm = _NativeEditFailsLLM()
    ex = _RecordingExecutor(succeed=False)  # the edit fails
    worker = AgentWorker(
        agent_name="Editor",
        agent_role="writer",
        llm=llm,
        tool_executor=ex,
        tool_schemas=[_EDIT_FILE_SCHEMA],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="writer",
            agent_name="Editor",
            task_description="Edit the report to fix the headline.",
            success_criteria="The report is corrected.",
            tools_hint=["edit_file"],
        ),
        shared_context="",
    )

    assert any(name == "edit_file" for name, _ in ex.calls)
    assert result.success is False, (
        "a failed edit_file (the only produce attempt) was wrongly marked success"
    )


# ============ C1: XML-invoke leak form (audit 2026-06-29) ============
# The bedrock/claude tool-use-via-XML spelling is
#   <invoke name="write_file"><parameter name="path">x.md</parameter>
#   <parameter name="content">...</parameter></invoke>
# This carries NO JSON object, so _extract_delimiter_adjacent_json (JSON-only)
# returned "" and F1 recovered nothing — the exact phantom-write that lost the
# radar task's c_uas_design.md report (task 79061802). F1 must parse this form.

# The literal Anthropic XML tool-call spelling, captured from the radar task leak.
_XML_INVOKE_LEAK = (
    "Tôi viết deliverable chính NGAY bằng MỘT write_file.\n\n"
    '<invoke name="write_file">\n'
    '<parameter name="path">report/c_uas_design.md</parameter>\n'
    '<parameter name="content"># Báo cáo Kỹ thuật\nThân báo cáo định lượng.</parameter>\n'
    "</invoke>"
)


class _XmlInvokeLeakLLM:
    """Native Claude/bedrock leak spelled as <invoke>/<parameter> XML in prose.

    Reproduces the radar-task phantom write: the model emits the tool call using
    the Anthropic XML tool-use spelling INSIDE content, native tool_calls empty.
    No JSON object is present, so the JSON-only recovery path misses it.
    """

    def __init__(self):
        self.tool_offered = 0

    async def chat(self, messages, tools=None, **kwargs):
        if not tools:
            return {"content": "Done.", "tool_calls": []}
        self.tool_offered += 1
        if self.tool_offered == 1:
            return {"content": _XML_INVOKE_LEAK, "tool_calls": []}
        return {"content": "The report is written. Done.", "tool_calls": []}


async def test_xml_invoke_leak_is_recovered():
    """F1: a genuine leak spelled as <invoke name=..><parameter..> must be executed."""
    llm = _XmlInvokeLeakLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert any(name == "write_file" for name, _ in ex.calls), (
        "an <invoke>-spelled genuine leak was dropped (radar-task phantom write)"
    )
    wrote = next(args for name, args in ex.calls if name == "write_file")
    assert wrote.get("path") == "report/c_uas_design.md"
    assert wrote.get("content", "").startswith("# Báo cáo Kỹ thuật"), (
        "the multi-line content parameter must survive intact"
    )
    assert result.success is True


# A deliverable that DOCUMENTS the XML spelling as an example (inside a fenced
# block, explained as illustrative). Like the JSON-example guard, an <invoke>
# quoted as documentation must NOT be executed.
_XML_DOC_REPORT = (
    "## How the writer agent calls a tool\n"
    "The agent emits the Anthropic XML spelling, for example:\n"
    "```xml\n"
    '<invoke name="write_file">\n'
    '<parameter name="path">report.md</parameter>\n'
    '<parameter name="content">...</parameter>\n'
    "</invoke>\n"
    "```\n"
    "This is illustrative only; the orchestrator routes the real call.\n"
)


class _DocumentsXmlExampleLLM:
    """Analysis report quoting an <invoke> example inside a fenced ```xml block."""

    async def chat(self, messages, tools=None, **kwargs):
        return {"content": _XML_DOC_REPORT, "tool_calls": []}


async def test_documented_xml_example_is_not_executed():
    """F1 guard: an <invoke> example inside a fenced doc block is not executed.

    A fenced ```xml block whose only surrounding text explains the protocol is
    documentation. The radar leak, by contrast, has the <invoke> hugging real
    content with no doc-fence framing. The recovery must distinguish them so a
    design/protocol doc that quotes the XML spelling is never misexecuted.
    """
    llm = _DocumentsXmlExampleLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = AgentWorker(
        agent_name="Agent Architect",
        agent_role="researcher",
        llm=llm,
        tool_executor=ex,
        tool_schemas=[_WRITE_FILE_SCHEMA],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="researcher",
            agent_name="Agent Architect",
            task_description="Document how the writer agent emits an <invoke> call.",
            success_criteria="A design note exists.",
        ),
        shared_context="",
    )

    assert not ex.calls, (
        "a documented <invoke> example was wrongly executed as a real tool call"
    )
    assert result.success is True


# ── C1a (regression, audit review 2026-06-29): a genuine <invoke> write whose
# CONTENT itself contains a ``` fence (any markdown/code deliverable) must keep
# that content intact. The first fix stripped all ``` fences from the whole
# message before parsing, gutting the write payload → empty file + false success.

_XML_LEAK_WITH_FENCE = (
    "Tôi viết script radar ngay.\n\n"
    '<invoke name="write_file">\n'
    '<parameter name="path">sim/radar.py</parameter>\n'
    '<parameter name="content">import numpy as np\n\n'
    "# Usage example, copied into the module docstring:\n"
    "```python\n"
    "budget = radar_budget(1000)\n"
    "```\n"
    'print(budget)</parameter>\n'
    "</invoke>"
)


class _XmlLeakWithFenceLLM:
    def __init__(self):
        self.tool_offered = 0

    async def chat(self, messages, tools=None, **kwargs):
        if not tools:
            return {"content": "Done.", "tool_calls": []}
        self.tool_offered += 1
        if self.tool_offered == 1:
            return {"content": _XML_LEAK_WITH_FENCE, "tool_calls": []}
        return {"content": "Script written. Done.", "tool_calls": []}


async def test_xml_invoke_leak_preserves_content_with_inner_fence():
    """C1a: a real <invoke> write whose content has a ``` fence is NOT gutted."""
    llm = _XmlLeakWithFenceLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = _worker(llm, ex)

    result = await worker.execute(assignment=_assignment(), shared_context="")

    assert any(name == "write_file" for name, _ in ex.calls), "real leak dropped"
    wrote = next(args for name, args in ex.calls if name == "write_file")
    content = wrote.get("content", "")
    # The whole body — including the fenced example and everything after it — must survive.
    assert "import numpy as np" in content
    assert "budget = radar_budget(1000)" in content, "fenced section eaten from content"
    assert "print(budget)" in content, "tail after the inner fence was lost"
    assert result.success is True


# ── C1b (regression): an UNCLOSED fence must still protect a documented <invoke>
# example after it (an opened ``` with no closing fence is still a doc block).

_XML_DOC_UNCLOSED_FENCE = (
    "Here is how a tool call looks (example, do not run):\n"
    "```\n"
    '<invoke name="write_file">\n'
    '<parameter name="path">should_not_run.md</parameter>\n'
    '<parameter name="content">nope</parameter>\n'
    "</invoke>\n"
)


class _DocUnclosedFenceLLM:
    async def chat(self, messages, tools=None, **kwargs):
        return {"content": _XML_DOC_UNCLOSED_FENCE, "tool_calls": []}


async def test_unclosed_fence_still_protects_documented_invoke():
    """C1b: an <invoke> under an unclosed ``` fence is documentation, not executed."""
    llm = _DocUnclosedFenceLLM()
    ex = _RecordingExecutor(succeed=True)
    worker = AgentWorker(
        agent_name="Agent Architect",
        agent_role="researcher",
        llm=llm,
        tool_executor=ex,
        tool_schemas=[_WRITE_FILE_SCHEMA],
    )

    result = await worker.execute(
        assignment=AgentAssignment(
            agent_role="researcher",
            agent_name="Agent Architect",
            task_description="Show an example <invoke> call in a fenced block.",
            success_criteria="A note exists.",
        ),
        shared_context="",
    )

    assert not ex.calls, "an <invoke> under an unclosed fence was wrongly executed"
    assert result.success is True


# ── C1d (round-4 regression): a genuine FIRST <invoke> whose content has an ODD
# number of ``` marks must NOT cause a genuine SECOND <invoke> to be dropped. The
# global fence-pairing in _fenced_spans counted the lone backtick as "fence open
# to EOF", swallowing the second real call (phantom write of the 2nd file).

from app.services.toolcall_recovery import _bare_xml_invoke_blocks
from app.services.prompted_tools import parse_xml_invoke_calls


def test_two_real_invokes_first_content_has_odd_backtick():
    """Two genuine write_file calls; the first's content mentions a single ```
    (odd) — the second call must still be recovered (not eaten as 'fenced')."""
    content = (
        '<invoke name="write_file">\n'
        '<parameter name="path">a.md</parameter>\n'
        '<parameter name="content">Use ``` to open a markdown fence.</parameter>\n'
        "</invoke>\n"
        '<invoke name="write_file">\n'
        '<parameter name="path">b.md</parameter>\n'
        '<parameter name="content">second file body</parameter>\n'
        "</invoke>"
    )
    calls = parse_xml_invoke_calls(_bare_xml_invoke_blocks(content), {"write_file"})
    paths = {json_loads_path(c) for c in calls}
    assert paths == {"a.md", "b.md"}, (
        f"odd backtick in the first content dropped the second invoke: got {paths}"
    )


def test_inline_odd_backtick_before_real_invoke_does_not_eat_it():
    """A lone inline ``` in prose BEFORE a genuine invoke must not gate it out."""
    content = (
        "Note: a fence starts with ``` normally.\n"
        '<invoke name="write_file">\n'
        '<parameter name="path">real.md</parameter>\n'
        '<parameter name="content">body</parameter>\n'
        "</invoke>"
    )
    calls = parse_xml_invoke_calls(_bare_xml_invoke_blocks(content), {"write_file"})
    assert len(calls) == 1 and json_loads_path(calls[0]) == "real.md"


def json_loads_path(call):
    import json
    return json.loads(call["function"]["arguments"]).get("path")


def test_doc_fence_closed_glued_to_code_does_not_eat_real_invoke():
    """C1e (round-4 regression): a doc fence whose CLOSING ``` is glued to the last
    code line (``print(1)```` ``) is not recognized as a line-leading close, so the
    in-fence state must not get stuck and swallow a genuine <invoke> after it.
    Recover-over-swallow: a real call must still be parsed."""
    content = (
        "Example code:\n"
        "```python\n"
        "print(1)```\n"
        '<invoke name="write_file">\n'
        '<parameter name="path">real.md</parameter>\n'
        '<parameter name="content">body</parameter>\n'
        "</invoke>"
    )
    calls = parse_xml_invoke_calls(_bare_xml_invoke_blocks(content), {"write_file"})
    assert len(calls) == 1 and json_loads_path(calls[0]) == "real.md", (
        "a fence-close glued to code stuck in_fence=True and ate the real invoke"
    )


def test_fully_fenced_doc_still_dropped_after_recover_net():
    """The recover-net must not resurrect a properly fenced doc example: when the
    ONLY <invoke> is inside a well-formed fence, nothing is recovered."""
    content = (
        "Example:\n"
        "```xml\n"
        '<invoke name="write_file">\n'
        '<parameter name="path">doc.md</parameter>\n'
        "</invoke>\n"
        "```\n"
        "End of example.\n"
    )
    calls = parse_xml_invoke_calls(_bare_xml_invoke_blocks(content), {"write_file"})
    assert calls == [], "a properly fenced doc example was wrongly recovered"
