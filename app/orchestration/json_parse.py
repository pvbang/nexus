"""Lenient JSON extraction from LLM responses.

Extracted from ``agent_worker`` (audit C1: shrink the god-object; also removes the
cross-module reach where ``turn_executor`` imported a private ``_parse_json_response``
out of ``agent_worker``). Pure, no I/O - directly unit-testable.

LLMs wrap JSON in prose or markdown fences inconsistently, and on big outputs
(e.g. an orchestrator plan with a deliverable_contract + full execution_plan) they
hit max_tokens and emit a TRUNCATED object. Audit A8 traced both real tasks'
collapse to a 1-researcher fallback plan back to this parser returning ``None`` on
exactly those shapes. The escalation ladder now is:
  1. direct parse
  2. fenced ```json``` block
  3. balanced top-level ``{...}`` object (not greedy first-brace, which a stray
     brace in prose breaks)
  4. lenient repair (strip trailing commas)
  5. truncation recovery (close a cut-off object, keeping its complete prefix keys)
Returns ``None`` only when nothing recoverable is present (callers decide fallback).

Note: this is the strict ``None``-on-failure variant. Other modules
(``visual_qa``, ``g4f_client``) keep their own parsers with domain-specific
fallback shapes - they are intentionally NOT merged here, since their callers
depend on those different fallbacks.
"""

from __future__ import annotations

import json
import re
from typing import Any


def _strip_trailing_commas(s: str) -> str:
    """Remove trailing commas before } or ] — STRING-AWARE.

    A blind ``re.sub(r",(\\s*[}\\]])", ...)`` would also delete a comma inside a
    string value (e.g. a shell command ``sed -i 's/x,]/y/'`` or any prose with
    ",]"/",}"), silently corrupting agent data. This walks the text tracking
    string/escape state and only drops a comma that is genuinely trailing (the
    next non-space structural char is ``}`` or ``]``).
    """
    out: list[str] = []
    in_str = False
    esc = False
    for i, c in enumerate(s):
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
            out.append(c)
        elif c == ",":
            # Look ahead past whitespace; drop the comma only if a closer follows.
            j = i + 1
            while j < len(s) and s[j] in " \t\r\n":
                j += 1
            if j < len(s) and s[j] in "}]":
                continue  # trailing comma — skip it
            out.append(c)
        else:
            out.append(c)
    return "".join(out)


def _balanced_objects(text: str):
    """Yield every brace-balanced ``{...}`` substring, respecting strings.

    Greedy ``{...}`` grabs from the first ``{`` anywhere (even one inside prose) to
    the last ``}``, which fails when the LLM wrote a brace in its preamble. This
    walks braces with string/escape awareness and yields each balanced object so a
    caller can try them in order (a non-JSON ``{role: x}`` in the preamble is
    skipped in favour of the real object that follows).
    """
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        j = i
        closed = False
        while j < n:
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    closed = True
                    break
            j += 1
        if not closed:
            # This ``{`` never closes (truncated). Do NOT rescan its interior for a
            # nested balanced object — a nested child is not the top-level plan, and
            # returning it would mask the truncation. Leave it to recovery (step 5).
            return
        i = j + 1


def _recover_truncated(text: str) -> dict[str, Any] | None:
    """Best-effort parse of a truncated JSON object, keeping complete prefix keys.

    When max_tokens cut the response mid-object, find the last position where the
    object was structurally complete (just past a closed value at depth >= 1), drop
    the dangling partial that follows, then close any open brackets/braces so the
    fully-formed earlier keys survive (a complex task keeps its real team/contract
    instead of collapsing to the fallback plan).
    """
    start = text.find("{")
    if start < 0:
        return None
    s = text[start:]

    # Track the last index that sits on a complete-pair boundary at the top level
    # (depth == 1): right after a "," (one pair finished) or after a nested
    # "}"/"]" closes back to depth 1. Cutting there guarantees we never keep a
    # half-written key or value (e.g. a key whose string was truncated).
    depth = 0
    in_str = False
    esc = False
    last_comma = -1   # index of the last top-level comma
    last_close = -1   # index just past the last nested close that returned to depth 1
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 1:
                last_close = i + 1
        elif c == "," and depth == 1:
            last_comma = i

    last_good = max(last_comma, last_close)
    if last_good <= 0:
        return None
    candidate = s[:last_good].rstrip().rstrip(",")

    # Close any brackets/braces left open, in the right order.
    stack: list[str] = []
    in_str = False
    esc = False
    for c in candidate:
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            stack.append("}")
        elif c == "[":
            stack.append("]")
        elif c in "}]" and stack:
            stack.pop()
    closed = candidate + "".join(reversed(stack))
    try:
        result = json.loads(_strip_trailing_commas(closed))
        return result if isinstance(result, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


def parse_json_response(text: str) -> dict[str, Any] | None:
    """Parse JSON from an LLM response, leniently. None only if unrecoverable."""
    if not text:
        return None

    # 1. Direct parse.
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Markdown code fence.
    fence = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text)
    fenced = fence.group(1) if fence else None
    if fenced:
        try:
            return json.loads(fenced)
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. Balanced top-level objects (handles a stray brace in the preamble). Parse
    #    every candidate and keep the RICHEST dict (most keys), so a small ack/example
    #    object before the real plan ({}, {"role":"x"}) never shadows the larger plan.
    best: dict[str, Any] | None = None
    for obj in _balanced_objects(text):
        parsed = None
        try:
            parsed = json.loads(obj)
        except (json.JSONDecodeError, TypeError):
            try:
                parsed = json.loads(_strip_trailing_commas(obj))
            except (json.JSONDecodeError, TypeError):
                parsed = None
        if isinstance(parsed, dict) and (best is None or len(parsed) > len(best)):
            best = parsed
    if best is not None:
        return best

    # 4b. Repair on the fenced body (e.g. trailing comma inside the fence).
    if fenced:
        try:
            return json.loads(_strip_trailing_commas(fenced))
        except (json.JSONDecodeError, TypeError):
            pass

    # 5. Truncation recovery - close a cut-off object, keep its complete prefix.
    #    Prefer the fenced body (closer to the JSON) then the raw text.
    for source in (fenced, text):
        if not source:
            continue
        recovered = _recover_truncated(source)
        if recovered:
            return recovered

    return None
