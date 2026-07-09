# Tool-call Recovery at Provider Boundary — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move phantom-write tool-call recovery (JSON-adjacency + XML-invoke) out of `agent_worker` into a pure shared module, and run it once at the provider boundary (`LLMClient._do_chat_call`) so every provider returns a uniform response shape.

**Architecture:** Extract the recovery helpers (currently in `agent_worker.py`) into a new pure module `app/services/toolcall_recovery.py` with a single entry point `recover_tool_calls(content, valid_names)`. `agent_worker._recover_tool_calls_from_content` becomes a thin delegate (kept for the F2 phantom-success net). `LLMClient._do_chat_call` calls `recover_tool_calls` when a native-provider turn returned no `tool_calls` but leaked one into `content` — gated on a non-safety `finish_reason` and the presence of `tools`.

**Tech Stack:** Python 3.13, pytest (`asyncio_mode = "auto"`), litellm + OpenAI-compat shim, structlog.

## Global Constraints

- Pure refactor of the moved helpers — **zero behavior change**; the existing `tests/test_phantom_write_recovery.py` (20 tests) must pass unchanged after the move.
- Boundary recovery only ever **adds** `tool_calls` when the slot is empty — it must never remove or alter a real `tool_calls`.
- Recovery gated: skip when `finish_reason` ∈ `SAFETY_FINISH_REASONS` (don't execute a content-filtered turn's prose) and when no `tools` were passed (no `valid_names`).
- Scope: non-streaming path only (`_do_chat_call`). Do NOT touch `_stream`, litellm config, or add a native SDK.
- `recover_tool_calls` is best-effort: wrap the body so it never raises into the LLM call.
- Preserve the 8-case `<invoke>` edge matrix: real leak; doc fence closed; doc fence unclosed; glued-close parity; inline backtick; content-with-fence; two-invoke odd-backtick; JSON-adjacency.
- Full suite must end green (currently 1077 passed / 4 skipped).
- Commit message trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

### Task 1: Create the pure `toolcall_recovery` module (move helpers verbatim)

**Files:**
- Create: `app/services/toolcall_recovery.py`
- Test: `tests/test_toolcall_recovery.py`

**Interfaces:**
- Consumes: `parse_tool_calls_from_text`, `parse_xml_invoke_calls` from `app.services.prompted_tools` (unchanged).
- Produces:
  - `recover_tool_calls(content: str, valid_names: set[str]) -> list[dict]`
  - module-level helpers (moved verbatim from `agent_worker.py`): `_iter_json_object_spans`, `_extract_delimiter_adjacent_json`, `_is_line_leading_fence`, `_scan_invoke_blocks`, `_bare_xml_invoke_blocks`, and constants `_TOOL_CALL_DELIMITERS`, `_FENCE_MARK`, `_INVOKE_OPEN`, `_INVOKE_CLOSE`.

- [ ] **Step 1: Write failing tests for the new module's public API**

Create `tests/test_toolcall_recovery.py`. These pin the 8-case matrix directly against `recover_tool_calls` (the entry point Task 1 creates):

```python
"""Unit tests for the pure tool-call recovery module (audit 2026-06-29).

These pin the JSON-adjacency + XML-invoke recovery behavior directly against the
public entry point, so the logic is tested in one place regardless of which
call site (llm_client boundary, or agent_worker F2 net) invokes it.
"""

from __future__ import annotations

import json

from app.services.toolcall_recovery import recover_tool_calls

_VALID = {"write_file", "edit_file", "read_file"}


def _paths(calls):
    return sorted(json.loads(c["function"]["arguments"]).get("path") for c in calls)


def test_real_xml_invoke_leak_recovered():
    content = (
        '<invoke name="write_file">\n'
        '<parameter name="path">r.md</parameter>\n'
        '<parameter name="content">hi</parameter>\n'
        "</invoke>"
    )
    calls = recover_tool_calls(content, _VALID)
    assert len(calls) == 1 and _paths(calls) == ["r.md"]


def test_content_with_inner_fence_preserved():
    content = (
        '<invoke name="write_file">'
        '<parameter name="path">s.py</parameter>'
        '<parameter name="content">import x\n```python\ndemo=1\n```\nprint(demo)</parameter>'
        "</invoke>"
    )
    body = json.loads(recover_tool_calls(content, _VALID)[0]["function"]["arguments"])["content"]
    assert "demo=1" in body and "print(demo)" in body


def test_doc_fence_closed_dropped():
    content = "ex:\n```xml\n<invoke name=\"write_file\"><parameter name=\"path\">no.md</parameter></invoke>\n```\nend"
    assert recover_tool_calls(content, _VALID) == []


def test_doc_fence_unclosed_dropped():
    content = "ex:\n```\n<invoke name=\"write_file\"><parameter name=\"path\">no.md</parameter></invoke>"
    assert recover_tool_calls(content, _VALID) == []


def test_glued_close_then_real_invoke_recovered():
    content = (
        "```python\nprint(1)```\n"
        '<invoke name="write_file"><parameter name="path">real.md</parameter>'
        '<parameter name="content">x</parameter></invoke>'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["real.md"]


def test_inline_backtick_before_real_invoke_recovered():
    content = (
        "use ``` to open a fence\n"
        '<invoke name="write_file"><parameter name="path">real.md</parameter>'
        '<parameter name="content">x</parameter></invoke>'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["real.md"]


def test_two_invokes_first_content_odd_backtick():
    content = (
        '<invoke name="write_file"><parameter name="path">a.md</parameter>'
        '<parameter name="content">use ``` here</parameter></invoke>\n'
        '<invoke name="write_file"><parameter name="path">b.md</parameter>'
        '<parameter name="content">x</parameter></invoke>'
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["a.md", "b.md"]


def test_json_adjacent_leak_recovered():
    content = (
        'I will write it.\n'
        '{"name": "write_file", "arguments": {"path": "j.md", "content": "body"}}\n'
        "</tool_call>"
    )
    assert _paths(recover_tool_calls(content, _VALID)) == ["j.md"]


def test_unknown_tool_rejected():
    content = '<invoke name="rm_rf"><parameter name="p">/</parameter></invoke>'
    assert recover_tool_calls(content, _VALID) == []


def test_empty_inputs_safe():
    assert recover_tool_calls("", _VALID) == []
    assert recover_tool_calls("just prose, no tool call", _VALID) == []
    assert recover_tool_calls("<invoke name=\"write_file\"></invoke>", set()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_toolcall_recovery.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.toolcall_recovery'`

- [ ] **Step 3: Create the module by moving helpers verbatim from agent_worker.py**

Create `app/services/toolcall_recovery.py`. Copy the following **verbatim** from `app/orchestration/agent_worker.py` (do not re-derive — these regressed three times; the audited versions are correct):
- The block `_TOOL_CALL_DELIMITERS = (...)` (currently `agent_worker.py:179-192`).
- `def _iter_json_object_spans(text):` (currently `:194-226`).
- `def _extract_delimiter_adjacent_json(content):` (currently `:228-266`).
- The constants `_FENCE_MARK = "```"`, `_INVOKE_OPEN = "<invoke"`, `_INVOKE_CLOSE = "</invoke>"` (currently `:281-283`).
- `def _is_line_leading_fence(content, pos):` (currently `:286-298`).
- `def _scan_invoke_blocks(content, low, respect_fences):` (currently `:300-331`).
- `def _bare_xml_invoke_blocks(content):` (currently `:333-...`).

Then add the module header and the public entry point (the body is the exact logic of the current `_recover_tool_calls_from_content`, minus the `self._valid_tool_names()` call — `valid_names` is now a parameter):

```python
"""Pure tool-call recovery for native-provider prose leaks (audit 2026-06-29).

A native provider (bedrock/claude via litellm) sometimes emits a tool call as a
JSON blob or as the Anthropic XML <invoke> spelling INSIDE its prose `content`
while leaving the native `tool_calls` slot empty — the phantom-write failure
mode. This module recovers such a leaked call so the boundary (llm_client) can
present a uniform response shape, and so the agent_worker F2 guard can reuse the
same logic. Pure: no agent or LLM-client state.

Two leak spellings are recovered, each behind its own structural gate:
  - JSON form (`{"name": ...}`) — gated by tool-call-delimiter adjacency.
  - Anthropic XML form (`<invoke name=..><parameter..>`) — gated by line-leading
    ``` fence membership with a parity-based recover-over-swallow net.
A parsed call is kept only if it names a tool in `valid_names`.
"""

from __future__ import annotations

import re
from typing import Any

from app.services.prompted_tools import (
    parse_tool_calls_from_text,
    parse_xml_invoke_calls,
)

# <<< moved constants + helper functions go here, verbatim >>>


def recover_tool_calls(content: str, valid_names: set[str]) -> list[dict[str, Any]]:
    """Recover tool-calls a native provider leaked into prose `content`.

    Returns native `tool_calls`-shaped dicts naming a tool in `valid_names`, or []
    when nothing actionable is present. Best-effort: never raises.
    """
    if not content or not valid_names:
        return []
    calls: list[dict[str, Any]] = []
    try:
        adjacent = _extract_delimiter_adjacent_json(content)
        if adjacent:
            calls.extend(parse_tool_calls_from_text(adjacent, valid_names))
    except Exception:
        pass
    try:
        bare = _bare_xml_invoke_blocks(content)
        if bare:
            calls.extend(parse_xml_invoke_calls(bare, valid_names))
    except Exception:
        pass
    return calls
```

Note: `_extract_delimiter_adjacent_json` and the XML helpers reference `re` and the moved constants — make sure `import re` is present (shown above) and all moved helpers land before `recover_tool_calls`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_toolcall_recovery.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add app/services/toolcall_recovery.py tests/test_toolcall_recovery.py
git commit -m "refactor(toolcall): extract pure tool-call recovery module

Move phantom-write recovery helpers (JSON-adjacency + XML-invoke, with the
line-leading-fence + parity gates) verbatim out of agent_worker into a pure
app/services/toolcall_recovery.py with a single recover_tool_calls() entry
point. No behavior change — direct unit tests pin the 8-case matrix.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Point `agent_worker` at the shared module (delete the moved copies)

**Files:**
- Modify: `app/orchestration/agent_worker.py` (delete moved helpers `:179-...:333-...`; rewrite `_recover_tool_calls_from_content` `:1816-1859`; fix imports)
- Test: `tests/test_phantom_write_recovery.py` (existing — must still pass unchanged)

**Interfaces:**
- Consumes: `recover_tool_calls` from `app.services.toolcall_recovery` (Task 1).
- Produces: `AgentWorker._recover_tool_calls_from_content(self, content) -> list[dict]` — same signature/behavior as before, now delegating.

- [ ] **Step 1: Run the existing phantom-write suite to capture the green baseline**

Run: `python -m pytest tests/test_phantom_write_recovery.py -q`
Expected: PASS (20 tests) — this is the behavior Task 2 must preserve.

- [ ] **Step 2: Delete the moved helpers from `agent_worker.py`**

Delete these definitions from `app/orchestration/agent_worker.py` (now living in `toolcall_recovery.py`): the `_TOOL_CALL_DELIMITERS` block, `_iter_json_object_spans`, `_extract_delimiter_adjacent_json`, the `_FENCE_MARK`/`_INVOKE_OPEN`/`_INVOKE_CLOSE` constants, `_is_line_leading_fence`, `_scan_invoke_blocks`, `_bare_xml_invoke_blocks`. Leave the surrounding `class AgentWorker` and unrelated module-level code intact.

- [ ] **Step 3: Rewrite `_recover_tool_calls_from_content` as a delegate**

Replace the body of `_recover_tool_calls_from_content` (`agent_worker.py:1816-1859`) with:

```python
    def _recover_tool_calls_from_content(self, content: str) -> list[dict[str, Any]]:
        """F1 net: recover a tool-call this worker's model leaked into prose.

        Boundary recovery in llm_client already covers the normal tooled turn;
        this remains for the F2 phantom-success guard, which inspects the
        tools-off wrap-up `final_content` (a synthesis call that may carry no
        `tools`, so the boundary couldn't recover it). Delegates to the shared
        pure module so the gating logic lives in exactly one place.
        """
        return recover_tool_calls(content, self._valid_tool_names())
```

- [ ] **Step 4: Fix imports in `agent_worker.py`**

In the `from app.services.prompted_tools import (...)` block (`:38-41`), remove `parse_tool_calls_from_text` and `parse_xml_invoke_calls` **only if** no other code in the file still uses them (grep first — Step 5 verifies). Add a new import:

```python
from app.services.toolcall_recovery import recover_tool_calls
```

Run this grep to decide what to keep:

```bash
grep -n "parse_tool_calls_from_text\|parse_xml_invoke_calls" app/orchestration/agent_worker.py
```

If the only remaining hits are the import line itself, drop those two names from the import (keep any other names imported from `prompted_tools`). If `re` is no longer referenced anywhere in `agent_worker.py` after the deletions, remove `import re` too (grep `\bre\.` to check).

- [ ] **Step 5: Run the existing suite + a broad regression check**

Run: `python -m pytest tests/test_phantom_write_recovery.py tests/test_assignment_loop_hash.py -q`
Expected: PASS (unchanged counts — proves the move was behavior-neutral)

Run: `python -c "import app.orchestration.agent_worker"`
Expected: no ImportError (catches a dangling reference to a deleted helper)

- [ ] **Step 6: Commit**

```bash
git add app/orchestration/agent_worker.py
git commit -m "refactor(agent_worker): delegate tool-call recovery to shared module

Delete the recovery helpers now living in toolcall_recovery; rewrite
_recover_tool_calls_from_content as a one-line delegate. Kept (not deleted)
for the F2 phantom-success guard on the tools-off wrap-up. Existing
phantom-write suite passes unchanged.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Recover at the provider boundary in `_do_chat_call`

**Files:**
- Modify: `app/services/llm_client.py` (`_do_chat_call`, between the `tool_calls` mapping `:752-764` and `sanitize_safety_finish` `:767`)
- Test: `tests/test_toolcall_recovery_boundary.py`

**Interfaces:**
- Consumes: `recover_tool_calls` (Task 1); `SAFETY_FINISH_REASONS` from `app.services.safety_finish_reason`.
- Produces: no new public symbol — `_do_chat_call` now populates `result["tool_calls"]` from a prose leak when the native slot was empty.

- [ ] **Step 1: Write failing boundary tests**

Create `tests/test_toolcall_recovery_boundary.py`. Drive a real `LLMClient._do_chat_call` with a fake litellm/openai response so the test exercises the actual boundary, not a mock of it:

```python
"""Boundary recovery in LLMClient._do_chat_call (audit 2026-06-29).

When a native provider returns no tool_calls but leaked one into `content`,
_do_chat_call recovers it so downstream sees a uniform shape. Gated on a
non-safety finish_reason and the presence of `tools`.
"""

from __future__ import annotations

import json
import types

import pytest

from app.services.llm_client import LLMClient


def _fake_response(content, tool_calls=None, finish_reason="stop"):
    """Build an object shaped like an openai/litellm ChatCompletion response."""
    msg = types.SimpleNamespace(
        content=content, role="assistant", tool_calls=tool_calls, reasoning=None
    )
    choice = types.SimpleNamespace(message=msg, finish_reason=finish_reason)
    usage = types.SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return types.SimpleNamespace(choices=[choice], usage=usage)


_WRITE_TOOL = {
    "type": "function",
    "function": {"name": "write_file", "parameters": {"type": "object", "properties": {}}},
}
_XML_LEAK = (
    '<invoke name="write_file">'
    '<parameter name="path">report/x.md</parameter>'
    '<parameter name="content">body</parameter>'
    "</invoke>"
)


def _client(monkeypatch, response):
    """An LLMClient whose underlying call returns `response`. litellm import is
    forced to fail so _do_chat_call uses the openai-client branch we stub."""
    client = LLMClient(provider="bedrock", model="anthropic.claude-opus-4-8")

    async def _fake_create(**kwargs):
        return response

    # Stub the openai-compat path; force the litellm branch to ImportError.
    monkeypatch.setattr(client.client.chat.completions, "create", _fake_create)
    import builtins
    real_import = builtins.__import__

    def _no_litellm(name, *a, **k):
        if name == "litellm":
            raise ImportError("forced for test")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_litellm)
    return client


async def test_xml_leak_recovered_at_boundary(monkeypatch):
    client = _client(monkeypatch, _fake_response(_XML_LEAK, tool_calls=None))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=[_WRITE_TOOL],
        tool_choice=None, response_format=None,
    )
    assert result.get("tool_calls"), "an XML-invoke leak was not recovered at the boundary"
    args = json.loads(result["tool_calls"][0]["function"]["arguments"])
    assert args["path"] == "report/x.md"


async def test_real_tool_calls_not_double_recovered(monkeypatch):
    real = [types.SimpleNamespace(
        id="c1", type="function",
        function=types.SimpleNamespace(name="write_file", arguments='{"path": "a.md"}'),
    )]
    client = _client(monkeypatch, _fake_response("ignored prose", tool_calls=real))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=[_WRITE_TOOL],
        tool_choice=None, response_format=None,
    )
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["id"] == "c1"  # untouched, not re-derived


async def test_safety_finish_blocks_recovery(monkeypatch):
    client = _client(monkeypatch, _fake_response(_XML_LEAK, tool_calls=None,
                                                 finish_reason="content_filter"))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=[_WRITE_TOOL],
        tool_choice=None, response_format=None,
    )
    assert not result.get("tool_calls"), "a content-filtered turn must not recover a tool call"


async def test_no_tools_means_no_recovery(monkeypatch):
    client = _client(monkeypatch, _fake_response(_XML_LEAK, tool_calls=None))
    result = await client._do_chat_call(
        messages=[{"role": "user", "content": "go"}],
        temperature=0.0, max_tokens=64, tools=None,
        tool_choice=None, response_format=None,
    )
    assert not result.get("tool_calls"), "no tools passed → no valid_names → no recovery"
```

- [ ] **Step 2: Run boundary tests to verify they fail**

Run: `python -m pytest tests/test_toolcall_recovery_boundary.py -q`
Expected: FAIL on `test_xml_leak_recovered_at_boundary` (no `tool_calls` recovered yet). The other three may pass already (they assert the *absence* of recovery); the failing one proves the gap.

- [ ] **Step 3: Add the boundary recovery block in `_do_chat_call`**

In `app/services/llm_client.py`, locate the tool-call mapping block that ends at `:764` (the `if hasattr(choice.message, "tool_calls") and choice.message.tool_calls:` block) and the `sanitize_safety_finish` import at `:767`. Insert **between** them:

```python
        # Boundary recovery (audit 2026-06-29): a native provider (bedrock/claude)
        # sometimes leaks a tool call into `content` as XML <invoke> or a JSON blob
        # while leaving the native tool_calls slot empty — the phantom-write mode.
        # Recover it here, once, so every downstream consumer sees a uniform shape.
        # Gated: skip when the turn was content-filtered (don't execute filtered
        # prose) and when no tools were offered (no valid names to validate against).
        if not result.get("tool_calls") and content and tools:
            _fr = str(result.get("finish_reason") or "").lower()
            if _fr not in SAFETY_FINISH_REASONS:
                _valid = {
                    name
                    for t in tools
                    if (name := (t.get("function", t) or {}).get("name"))
                }
                if _valid:
                    _recovered = recover_tool_calls(content, _valid)
                    if _recovered:
                        result["tool_calls"] = _recovered
                        logger.warning(
                            "toolcall_recovered_at_boundary",
                            provider=self.provider,
                            model=self.model,
                            count=len(_recovered),
                            tools=[c["function"]["name"] for c in _recovered],
                        )
```

- [ ] **Step 4: Add the imports in `llm_client.py`**

At the top of `app/services/llm_client.py`, add to the module imports:

```python
from app.services.toolcall_recovery import recover_tool_calls
from app.services.safety_finish_reason import SAFETY_FINISH_REASONS
```

(`SAFETY_FINISH_REASONS` is currently imported lazily inside `_do_chat_call` only as `sanitize_safety_finish` at `:767`; a module-level import of the constant is fine and avoids a per-call import. Leave the existing `from app.services.safety_finish_reason import sanitize_safety_finish` line at `:767` untouched.)

- [ ] **Step 5: Run boundary tests to verify they pass**

Run: `python -m pytest tests/test_toolcall_recovery_boundary.py -q`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS — 1081 passed (1077 prior + 4 boundary; the 11 module tests and 20 phantom tests are already counted), 4 skipped, 0 failed.

- [ ] **Step 7: Commit**

```bash
git add app/services/llm_client.py tests/test_toolcall_recovery_boundary.py
git commit -m "fix(llm_client): recover leaked tool-calls at the provider boundary

_do_chat_call now recovers a tool call a native provider leaked into prose
(XML <invoke> or JSON blob) when the native tool_calls slot is empty — once,
where every provider passes through. Gated on a non-safety finish_reason and
the presence of tools, and only ever fills an empty slot. This is the
root-cause fix for the radar-task phantom write; downstream recovery in
agent_worker is now a redundant F2 net rather than the primary path.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Verify against the radar-task leak + adversarial review

**Files:** none (verification only)

- [ ] **Step 1: Reproduce the radar-task XML-invoke leak through the boundary**

Run this end-to-end check (UTF-8 for the Vietnamese content):

```bash
PYTHONIOENCODING=utf-8 python -c "
import json
from app.services.toolcall_recovery import recover_tool_calls
leak = '''Tôi viết deliverable chính NGAY.
<invoke name=\"write_file\">
<parameter name=\"path\">report/c_uas_design.md</parameter>
<parameter name=\"content\"># Báo cáo
SNR@1km = +13.85 dB
\`\`\`python
budget = 1
\`\`\`
print(budget)</parameter>
</invoke>'''
calls = recover_tool_calls(leak, {'write_file','read_file'})
c = json.loads(calls[0]['function']['arguments'])
assert len(calls)==1 and c['path']=='report/c_uas_design.md'
assert 'budget = 1' in c['content'] and 'print(budget)' in c['content']
print('OK radar leak recovers; fenced content intact')
"
```
Expected: `OK radar leak recovers; fenced content intact`

- [ ] **Step 2: Confirm the boundary gate cannot corrupt a real call**

Already covered by `test_real_tool_calls_not_double_recovered` (Task 3) — re-run it explicitly:

Run: `python -m pytest tests/test_toolcall_recovery_boundary.py::test_real_tool_calls_not_double_recovered -q`
Expected: PASS

- [ ] **Step 3: Adversarial review of the moved module**

Dispatch one adversarial reviewer over `app/services/toolcall_recovery.py` and the `_do_chat_call` boundary block. This code regressed three times (C1a/C1d/C1e); re-audit after the move. Focus: did the verbatim move drop or reorder any helper? Does the boundary block read `tools` correctly when `tools` is a list of `{"type":"function","function":{...}}` vs a bare `{"name":...}` schema? Any path where boundary recovery fires on a real-`tool_calls` turn?

Record findings in `workspace_data/tasks_audit_2026-06-29/review-boundary.md`. Fix any real bug with a failing test first (TDD), then re-run the full suite.

- [ ] **Step 4: Final full-suite gate**

Run: `python -m pytest tests/ -q -p no:cacheprovider`
Expected: PASS — 0 failed.

---

## Self-Review (completed by plan author)

**Spec coverage:** New module (Task 1) ✓ · agent_worker delegate kept for F2 (Task 2) ✓ · boundary recovery in `_do_chat_call` with safety + tools gate (Task 3) ✓ · 8-case matrix preserved (Task 1 tests) ✓ · refactor-is-pure check (Task 2 Step 5) ✓ · radar-leak verification + adversarial review (Task 4) ✓ · YAGNI: streaming/litellm/native-SDK untouched ✓.

**Placeholder scan:** No TBD/TODO; every code step shows full code; commands have expected output.

**Type consistency:** `recover_tool_calls(content: str, valid_names: set[str]) -> list[dict]` is defined in Task 1 and consumed identically in Task 2 (delegate) and Task 3 (boundary). `_valid_tool_names()` (existing, `agent_worker.py:1803`) returns `set[str]` — matches the `valid_names` param. Boundary builds the same set inline from `tools`. `SAFETY_FINISH_REASONS` is a `frozenset[str]` (verified in `safety_finish_reason.py`).
