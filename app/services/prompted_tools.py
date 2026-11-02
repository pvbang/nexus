"""
Nexus - Prompted (text-based) tool-calling fallback.

Free / weak LLM providers (g4f, Pollinations, and many anonymous gateways)
do not implement OpenAI-style native function-calling. When the fallback
chain lands on such a provider, the agent loop would receive an empty
`tool_calls` list and wrongly conclude the agent is "done" — the symptom
where agents only "think" about using tools but never execute any.

This module closes that gap with the ReAct-style prompted-tools pattern:

1. ``supports_native_tools`` — decide whether a provider/model can use the
   API's structured function-calling, based on provider capability (NOT on
   inspecting responses).
2. ``build_tool_instructions`` — render the tool schemas + an exact call
   format into a system-prompt section, so a prompted model knows which
   tools exist and how to invoke them.
3. ``parse_tool_calls_from_text`` — extract tool calls the model emitted as
   text and return them in the SAME shape as native ``tool_calls`` so the
   rest of the pipeline (agent_worker, tool_executor) is unchanged.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Optional

import structlog

logger = structlog.get_logger(__name__)


# Providers whose APIs implement OpenAI-style native function-calling.
# Anything not listed here is driven via prompted (text) tool-calling.
# The registry entries with native_tools=True (deepseek, groq, xai, mistral, ...)
# are merged in so a new OpenAI-compatible provider only needs ONE registry edit.
from app.services.llm_providers import OPENAI_COMPATIBLE_PROVIDERS

_NATIVE_TOOL_PROVIDERS = frozenset(
    {"bedrock", "openai", "gemini", "openrouter", "local"}
    | {name for name, spec in OPENAI_COMPATIBLE_PROVIDERS.items() if spec.native_tools}
)

# Providers known to lack reliable native function-calling.
_PROMPTED_TOOL_PROVIDERS = frozenset({"g4f", "pollinations"})


def supports_native_tools(provider: str, model: str = "") -> bool:
    """Return True if the provider/model supports native function-calling.

    Decision is based on provider capability, not on parsing any response.
    Kilo Gateway proxies heterogeneous upstreams of varying quality, so it
    is treated as prompted to stay on the safe side.
    """
    p = (provider or "").lower().strip()
    if p in _PROMPTED_TOOL_PROVIDERS:
        return False
    return p in _NATIVE_TOOL_PROVIDERS


# ── Instruction rendering ────────────────────────────────────────────────


_CALL_FORMAT_GUIDE = """\
## HOW TO USE TOOLS

You do NOT have native function-calling. To call a tool, emit a fenced code
block tagged `tool_call` containing a single JSON object with `name` and
`arguments`. You may emit multiple blocks to call several tools in one turn.

Format (copy exactly):
```tool_call
{"name": "<tool_name>", "arguments": {"<param>": "<value>"}}
```

Rules:
- `arguments` MUST be a JSON object (use `{}` if the tool takes no params).
- Emit a `tool_call` block whenever you need to act — do not merely describe
  the action in prose; prose alone does NOTHING.
- After you receive tool results, continue with more `tool_call` blocks or,
  when the task is fully complete, reply with your final answer and NO
  `tool_call` blocks.
"""


def build_tool_instructions(tools: Optional[list[dict[str, Any]]]) -> str:
    """Render tool schemas + call-format guide for a prompted model.

    Args:
        tools: Tool schemas in OpenAI/registry format
            ``{"type": "function", "function": {"name", "description",
            "parameters"}}``.

    Returns:
        A system-prompt section, or "" when there are no tools.
    """
    if not tools:
        return ""

    lines: list[str] = ["## AVAILABLE TOOLS", ""]
    for tool in tools:
        fn = tool.get("function", tool)
        name = fn.get("name")
        if not name:
            continue
        desc = (fn.get("description") or "").strip()
        lines.append(f"### {name}")
        if desc:
            lines.append(desc)

        params = (fn.get("parameters") or {}).get("properties") or {}
        required = set((fn.get("parameters") or {}).get("required") or [])
        if params:
            lines.append("Parameters:")
            for pname, pinfo in params.items():
                ptype = pinfo.get("type", "any") if isinstance(pinfo, dict) else "any"
                pdesc = pinfo.get("description", "") if isinstance(pinfo, dict) else ""
                flag = "required" if pname in required else "optional"
                detail = f" — {pdesc}" if pdesc else ""
                lines.append(f"- `{pname}` ({ptype}, {flag}){detail}")
        lines.append("")

    return _CALL_FORMAT_GUIDE + "\n" + "\n".join(lines)


# ── History flattening ───────────────────────────────────────────────────


def flatten_tool_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert structured tool_call / tool-result messages into plain text.

    Prompted providers don't understand assistant ``tool_calls`` or
    ``role="tool"`` messages. On multi-round runs the conversation history
    accumulates these, so before sending to a prompted provider we render
    them as text while preserving their semantic content:

      - assistant message with tool_calls → assistant text "[Called tool: ...]"
      - tool-result message → user text "[Tool result: ...]"

    Returns the original list unchanged when there is nothing to flatten.
    """
    if not messages:
        return messages

    has_tools = any(
        msg.get("tool_calls") or msg.get("role") == "tool" for msg in messages
    )
    if not has_tools:
        return messages

    result: list[dict[str, Any]] = []
    pending_results: list[str] = []

    def _flush() -> None:
        if pending_results:
            result.append({"role": "user", "content": "\n".join(pending_results)})
            pending_results.clear()

    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant" and msg.get("tool_calls"):
            _flush()
            parts: list[str] = []
            if msg.get("content"):
                parts.append(str(msg["content"]))
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "unknown_tool")
                args = fn.get("arguments", "") or ""
                if len(args) > 500:
                    args = args[:500] + "..."
                parts.append(f"[Called tool: {name}({args})]")
            result.append({"role": "assistant", "content": "\n".join(parts)})

        elif role == "tool":
            tool_content = msg.get("content", "") or ""
            if len(tool_content) > 2000:
                tool_content = tool_content[:2000] + "...[truncated]"
            pending_results.append(f"[Tool result: {tool_content}]")

        else:
            _flush()
            result.append(msg)

    _flush()
    return result


# ── Text parsing ─────────────────────────────────────────────────────────


# Fenced ```tool_call ... ``` blocks (the format we instruct models to emit).
_FENCED_TOOL_CALL_RE = re.compile(
    r"```(?:tool_call|json)?\s*\n(.*?)\n?```",
    re.DOTALL | re.IGNORECASE,
)


def _coerce_to_call(obj: Any, valid_names: set[str]) -> Optional[dict[str, Any]]:
    """Turn a parsed JSON object into a native-format tool_call dict.

    Returns None if the object is not a valid tool call for a known tool.
    """
    if not isinstance(obj, dict):
        return None

    name = obj.get("name")
    if not isinstance(name, str) or name not in valid_names:
        return None

    # Accept both "arguments" (OpenAI) and "parameters" (common LLM drift).
    args = obj.get("arguments")
    if args is None:
        args = obj.get("parameters")
    if args is None:
        args = {}

    # Native format requires arguments as a JSON STRING.
    if isinstance(args, str):
        args_str = args
    else:
        try:
            args_str = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            return None

    return {
        "type": "function",
        "function": {"name": name, "arguments": args_str},
    }


def _iter_json_objects(text: str) -> Iterable[Any]:
    """Yield top-level JSON objects found in text via balanced-brace scan.

    Handles braces inside strings so nested ``arguments`` objects are parsed
    as one unit rather than being split.
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
                    snippet = text[start : i + 1]
                    try:
                        yield json.loads(snippet)
                    except (json.JSONDecodeError, ValueError):
                        pass
                    start = -1


# Anthropic/Claude tool-use-via-XML spelling:
#   <invoke name="write_file">
#     <parameter name="path">x.md</parameter>
#     <parameter name="content">...</parameter>
#   </invoke>
# A native bedrock/claude turn occasionally leaks a call in this form into prose
# (the phantom-write failure mode). This spelling carries NO JSON object, so the
# JSON scanners above cannot see it — it needs its own structural parser.
_XML_INVOKE_RE = re.compile(
    r"<invoke\b[^>]*\bname\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</invoke\s*>",
    re.DOTALL | re.IGNORECASE,
)
_XML_PARAMETER_RE = re.compile(
    r"<parameter\b[^>]*\bname\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</parameter\s*>",
    re.DOTALL | re.IGNORECASE,
)


def parse_xml_invoke_calls(
    content: Optional[str],
    valid_names: set[str],
) -> list[dict[str, Any]]:
    """Extract tool calls spelled in the Anthropic <invoke>/<parameter> XML form.

    Mirrors the contract of ``parse_tool_calls_from_text``: only ``<invoke>``
    blocks naming a tool in ``valid_names`` are returned, in native ``tool_calls``
    shape. Parameter values are kept as raw strings (the native arguments slot is
    a JSON string), which preserves multi-line content verbatim.

    Returns [] when nothing parseable is present.
    """
    if not content or not isinstance(content, str):
        return []

    calls: list[dict[str, Any]] = []
    for name, body in _XML_INVOKE_RE.findall(content):
        if name not in valid_names:
            continue
        args: dict[str, str] = {}
        for pname, pval in _XML_PARAMETER_RE.findall(body):
            # Strip a single leading/trailing newline the XML formatting adds,
            # but preserve interior whitespace of multi-line content verbatim.
            args[pname] = pval.strip("\n")
        try:
            args_str = json.dumps(args, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        calls.append(
            {
                "type": "function",
                "function": {"name": name, "arguments": args_str},
            }
        )

    for idx, call in enumerate(calls):
        call["id"] = f"call_xml_{idx}"

    if calls:
        logger.info(
            "xml_invoke_tool_calls_parsed",
            count=len(calls),
            tools=[c["function"]["name"] for c in calls],
        )
    return calls


# ── Display-time markup stripping ──────────────────────────────────────────
#
# The reasoning text shown on the timeline is the model's prose `content`, which
# frequently carries the serialized tool call alongside it (the model's call
# machinery leaks into prose). Each model family spells that machinery
# differently, so cleaning the display must be FORMAT-AGNOSTIC across families —
# tagged XML blocks AND the pipe-wrapped special tokens that chat templates
# (DeepSeek/Qwen-style) emit. This is display-only; it never affects what the
# agent actually executes (that path is parse_*/recover_tool_calls above).

# Tagged tool-call blocks (paired) — removed wholesale, prose around them kept.
_DISPLAY_BLOCK_PATTERNS = [
    re.compile(r"```tool_call\b.*?```", re.DOTALL | re.IGNORECASE),
    re.compile(r"<function_calls>.*?</function_calls>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<invoke\b[^>]*>.*?</invoke\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<parameter\b[^>]*>.*?</parameter\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<tool_use\b[^>]*>.*?</tool_use\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<function>.*?</function>", re.DOTALL | re.IGNORECASE),
    # A model that leaks a tool CALL often also fabricates the tool RESPONSE
    # (the system never emits <tool_response> into the model's own content). Left
    # in prose it becomes a self-authored "File written ✅" lie that later turns
    # read as ground truth, spreading the phantom-write (E8, run 3a75a0db).
    re.compile(r"<tool_response>.*?</tool_response\s*>", re.DOTALL | re.IGNORECASE),
]

# Pipe-wrapped special tokens: ASCII bar ``<|`` or fullwidth bar ``<｜`` (U+FF5C).
# A chat template emits these (``<|tool_call|>``, ``<｜tool▁calls▁begin｜>``,
# the ``<｜DSML｜function_calls`` family the user observed) right where the model
# stops reasoning and starts serializing the call. They are never valid prose, so
# the reasoning ends at the first one — truncating there hides the whole tail.
_PIPE_TOKEN_RE = re.compile(r"<[|｜]")


def strip_tool_call_markup(text: Optional[str]) -> Optional[str]:
    """Remove leaked tool-call machinery from prose for clean display.

    Format-agnostic across model families: drops tagged blocks (fenced
    ``tool_call``, Anthropic ``<invoke>``/``<function_calls>``, ``<tool_use>``)
    and truncates at the first pipe-wrapped chat-template token (``<|…|>`` /
    ``<｜…｜>``). Returns the input unchanged when there is no markup, and passes
    through ``None``/``""`` untouched. Display-only — never alters execution.
    """
    if not text:
        return text

    cleaned = text
    for pat in _DISPLAY_BLOCK_PATTERNS:
        cleaned = pat.sub("", cleaned)

    m = _PIPE_TOKEN_RE.search(cleaned)
    if m:
        cleaned = cleaned[: m.start()]

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_tool_calls_from_text(
    content: Optional[str],
    valid_names: set[str],
) -> list[dict[str, Any]]:
    """Extract tool calls a prompted model emitted as text.

    Strategy:
      1. Parse any fenced ```tool_call```/```json``` blocks.
      2. If none matched, scan the whole text for bare JSON tool-call objects.

    Only objects naming a tool in ``valid_names`` are returned, which prevents
    unrelated JSON or prose mentioning a tool from being misread as a call.

    Returns calls in native ``tool_calls`` shape, each with a unique ``id``.
    """
    if not content or not isinstance(content, str):
        return []

    calls: list[dict[str, Any]] = []

    # 1. Fenced blocks (preferred — what we instruct the model to emit).
    for block in _FENCED_TOOL_CALL_RE.findall(content):
        for obj in _iter_json_objects(block):
            call = _coerce_to_call(obj, valid_names)
            if call:
                calls.append(call)

    # 2. Fallback: scan whole text for bare JSON tool-call objects.
    if not calls:
        for obj in _iter_json_objects(content):
            call = _coerce_to_call(obj, valid_names)
            if call:
                calls.append(call)

    # Assign unique IDs matching the native format (call_<n>).
    for idx, call in enumerate(calls):
        call["id"] = f"call_prompted_{idx}"

    if calls:
        logger.info(
            "prompted_tool_calls_parsed",
            count=len(calls),
            tools=[c["function"]["name"] for c in calls],
        )
    return calls
