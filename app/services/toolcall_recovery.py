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

# Explicit tool-call delimiters that a native model's tool-calling machinery emits
# when it MEANT to call a tool but the call leaked into prose (the phantom-write
# failure mode). A documentation example of a tool call inside a generic ```json
# fence carries none of these, so requiring one of these markers lets F1 recover a
# genuine leaked call without misexecuting a quoted example. Matched case-insensitively.
_TOOL_CALL_DELIMITERS = (
    "```tool_call",
    "<tool_call>", "</tool_call>",
    # Anthropic/Claude native: tool calls are content blocks of type "tool_use",
    # and the tool-use-via-XML spelling uses <function_calls>/<invoke>. The live
    # provider is bedrock/claude-opus, so a leaked call most plausibly reflects
    # one of these names.
    "<tool_use", "</tool_use>",
    "<function_calls>", "<invoke",
    # Pipe-wrapped (Qwen/DeepSeek-style chat templates that openrouter/local proxy).
    # Both the ASCII bar `|` (U+007C) and the fullwidth bar `｜` (U+FF5C) appear:
    # DeepSeek's template serializes calls with fullwidth bars and a U+2581
    # word-joiner (`<｜tool▁calls▁begin｜>` … `<｜tool▁sep｜>`). A rotation model
    # served through a proxy that does not normalize these special tokens into the
    # native tool_calls slot leaks the JSON next to one of them; listing the
    # markers lets the adjacency gate recover it (the JSON still validates by name).
    "<|tool_call|>",
    "<｜tool▁call｜>", "<｜tool▁calls▁begin｜>", "<｜tool▁call▁begin｜>",
    "<｜tool▁sep｜>", "<｜tool▁call▁end｜>", "<｜tool▁calls▁end｜>",
    "[tool_call]", "[/tool_call]",
)


def _iter_json_object_spans(text: str):
    """Yield (start, end_exclusive) of top-level ``{...}`` objects, string-aware.

    Mirrors ``prompted_tools._iter_json_objects`` but returns positions so callers
    can test a JSON object's adjacency to a surrounding delimiter. Braces inside
    JSON strings are ignored so a nested ``arguments`` object is one span.
    """
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield (start, i + 1)
                    start = -1


def _extract_delimiter_adjacent_json(content: str) -> str:
    """Return JSON objects structurally ADJACENT to a tool-call delimiter.

    A genuine leaked native tool call has its delimiter hugging the JSON object —
    only whitespace separates them (``{...}\\n</tool_call>`` or ```` ```tool_call\\n{...} ````).
    A documentation example sits inside a generic ```` ```json ```` fence with the
    delimiter, if mentioned at all, in distant prose; its JSON has no adjacent
    delimiter. Requiring adjacency (not mere co-occurrence) is what lets F1 recover
    a real leak without misreading a report that *explains* the tool-call protocol.

    Returns the adjacent JSON substrings joined by newlines (fed to
    ``parse_tool_calls_from_text`` for coercion + name validation), or "".
    """
    if not content:
        return ""
    low = content.lower()
    # Precompute every delimiter occurrence span [(start, end_exclusive), ...].
    delim_spans: list[tuple[int, int]] = []
    for d in _TOOL_CALL_DELIMITERS:
        pos = 0
        while True:
            idx = low.find(d, pos)
            if idx == -1:
                break
            delim_spans.append((idx, idx + len(d)))
            pos = idx + len(d)
    if not delim_spans:
        return ""

    blobs: list[str] = []
    for obj_start, obj_end in _iter_json_object_spans(content):
        adjacent = False
        for d_start, d_end in delim_spans:
            # Delimiter immediately PRECEDES the object (whitespace-only gap).
            if d_end <= obj_start and not content[d_end:obj_start].strip():
                adjacent = True
                break
            # Delimiter immediately FOLLOWS the object (whitespace-only gap).
            if d_start >= obj_end and not content[obj_end:d_start].strip():
                adjacent = True
                break
        if adjacent:
            blobs.append(content[obj_start:obj_end])
    return "\n".join(blobs)


# Fenced code blocks (```...```) gate documented XML-invoke examples. A genuine
# native leak emits the <invoke> spelling in BARE prose (the model's tool-calling
# machinery); a design/protocol doc quotes it INSIDE a fence as an illustration.
# We use fence state ONLY to decide whether a given <invoke> is inside a doc block
# — we never mutate the message text, because a real write payload
# (``<parameter name="content">``) routinely contains ``` fences itself, and
# stripping those would gut the deliverable (regression C1a, audit 2026-06-29).
_FENCE_MARK = "```"
_INVOKE_OPEN = "<invoke"
_INVOKE_CLOSE = "</invoke>"


def _is_line_leading_fence(content: str, pos: int) -> bool:
    """Whether a ``` at ``pos`` opens/closes a Markdown fence (line-leading).

    A code fence is recognized only at the START of a line (after optional
    indent), not mid-sentence — so a backtick run inside prose ("use ``` to open a
    fence") does NOT toggle fence state, while a real ```-fenced doc example does."""
    if not content.startswith(_FENCE_MARK, pos):
        return False
    j = pos - 1
    while j >= 0 and content[j] in " \t":
        j -= 1
    return j < 0 or content[j] == "\n"


def _scan_invoke_blocks(
    content: str, low: str, respect_fences: bool
) -> tuple[list[str], bool]:
    """Collect ``<invoke>…</invoke>`` blocks verbatim. When ``respect_fences`` is
    True, blocks whose open sits inside a line-leading ``` fence are dropped (doc
    examples). The scan SKIPS past each invoke block so a ``` inside a write
    payload never touches fence state (regression C1d).

    Returns ``(blocks, ended_in_fence)`` — ``ended_in_fence`` is True when the scan
    finished with an UNBALANCED open fence (a ragged/glued close that was not
    recognized line-leading), which is the signal the recover-net keys on."""
    blocks: list[str] = []
    i = 0
    in_fence = False
    n = len(content)
    while i < n:
        if respect_fences and _is_line_leading_fence(content, i):
            in_fence = not in_fence
            i += len(_FENCE_MARK)
            continue
        if low.startswith(_INVOKE_OPEN, i):
            close = low.find(_INVOKE_CLOSE, i)
            if close == -1:
                break  # unterminated <invoke> — not parseable, stop
            end = close + len(_INVOKE_CLOSE)
            if not in_fence:
                blocks.append(content[i:end])
            i = end
            continue
        i += 1
    return blocks, in_fence


def _bare_xml_invoke_blocks(content: str) -> str:
    """Return the ``<invoke>…</invoke>`` blocks that are NOT inside a fenced doc
    block, joined verbatim, with their text preserved byte-for-byte.

    Round-4 fixes: a single left-to-right scan breaks the chicken-and-egg between
    fences and invoke payloads — fence ``` marks toggle state only when
    line-leading (so a backtick in prose or in a write payload cannot flip it), and
    each invoke block is skipped wholesale (so a ``` inside a content parameter can
    neither flip fence state nor get a following real <invoke> swallowed).

    RECOVER-OVER-SWALLOW net (C1e): markdown fence CLOSINGS have ragged forms (a
    ``` glued to the last code line is not line-leading), which can leave the fence
    state stuck open and swallow every real <invoke> after it. When the
    fence-respecting scan yields nothing AND the swallowed invoke had a CLOSE-attempt
    before it (an EVEN number of ``` marks, line-leading or not, precede it — i.e. a
    fence was opened and an attempt was made to close it), re-scan ignoring fences:
    a phantom write is worse than executing a recovered call (which still passes F1
    name-validation + the F2 guard). A doc whose invoke sits under a still-OPEN
    fence (ODD marks before it, e.g. ```\\n<invoke> with no close) stays dropped."""
    low = content.lower() if content else ""
    if not content or _INVOKE_OPEN not in low:
        return ""

    blocks, ended_in_fence = _scan_invoke_blocks(content, low, respect_fences=True)
    if not blocks and ended_in_fence:
        # The fence state got stuck open. Distinguish a ragged CLOSE (recover) from
        # a doc example under a genuinely-open fence (drop) by the parity of ALL ```
        # marks before the first invoke: even ⇒ a close was attempted ⇒ recover.
        first_invoke = low.find(_INVOKE_OPEN)
        marks_before = content.count(_FENCE_MARK, 0, first_invoke)
        if first_invoke != -1 and marks_before % 2 == 0 and marks_before > 0:
            blocks, _ = _scan_invoke_blocks(content, low, respect_fences=False)
    return "\n".join(blocks)


# R2-F (tri-run audit round-2, 2026-07-03): the DeepSeek/GLM-style template where a
# ``<tool_call>`` block carries the tool NAME as a bare token (not JSON, not
# ``<invoke name=..>``) followed by ``<arg_key>k</arg_key><arg_value>v</arg_value>``
# pairs. Neither existing branch recovered it (JSON needs ``name`` inside the object;
# ``<invoke>`` is a different spelling), so ~30 such calls in task-03ce93bd executed
# ZERO. This parser recovers name + well-formed arg pairs; malformed/valueless keys
# are skipped rather than crashing (the run's list_files had ragged, valueless keys).
_TOOLCALL_BLOCK_RE = re.compile(r"<tool_call>\s*(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_ARG_PAIR_RE = re.compile(
    r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
    re.DOTALL | re.IGNORECASE,
)
_TOOLNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")


def _parse_argkey_tool_calls(content: str, valid_names: set[str]) -> list[dict[str, Any]]:
    """Recover ``<tool_call>NAME <arg_key>/<arg_value>...`` blocks (R2-F).

    The tool name is the first bare identifier token inside the block; arguments are
    the well-formed ``<arg_key>k</arg_key><arg_value>v</arg_value>`` pairs (a key
    with no matching value is skipped — the model emits ragged tags). A block whose
    name is not in ``valid_names`` is dropped. Best-effort, never raises.
    """
    import json as _json

    calls: list[dict[str, Any]] = []
    for block in _TOOLCALL_BLOCK_RE.findall(content):
        head = block.lstrip()
        m = _TOOLNAME_RE.match(head)
        if not m:
            continue
        name = m.group(0)
        if name not in valid_names:
            continue
        args: dict[str, str] = {}
        for k, v in _ARG_PAIR_RE.findall(block):
            k = k.strip()
            # Ignore an arg_key that is itself a stray tag fragment or empty.
            if k and "<" not in k and k not in args:
                args[k] = v.strip()
        calls.append({
            "id": f"recovered_argkey_{len(calls)}",
            "type": "function",
            "function": {"name": name, "arguments": _json.dumps(args, ensure_ascii=False)},
        })
    return calls


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
    # R2-F: the <tool_call>NAME + <arg_key>/<arg_value> template. Only add calls the
    # JSON/XML branches did not already recover (dedupe by name+args).
    try:
        seen = {(c["function"]["name"], c["function"]["arguments"]) for c in calls}
        for c in _parse_argkey_tool_calls(content, valid_names):
            key = (c["function"]["name"], c["function"]["arguments"])
            if key not in seen:
                seen.add(key)
                calls.append(c)
    except Exception:
        pass
    return calls
