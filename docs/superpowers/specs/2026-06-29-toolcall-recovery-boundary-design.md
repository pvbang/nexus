# Centralize tool-call recovery at the provider boundary

**Date:** 2026-06-29
**Status:** Design — approved, pending spec review
**Author:** Claude (Opus 4.8) + pvbang

## Problem

mekongai calls Claude-on-Bedrock (the default provider) through a **litellm +
OpenAI-compat shim** (`AsyncOpenAI`, `litellm.acompletion`, `modify_params=True`).
Claude returns a tool call as a native `tool_use` content block; litellm maps it
to OpenAI-shaped `choice.message.tool_calls`. The **phantom-write** failure mode
happens when the model instead emits the tool call as XML `<invoke>` (or a JSON
blob) **inside `content` prose** — then `tool_calls` is empty
(`llm_client.py:752-764`), the worker treats the prose as a final answer, reports
`success=True`, and nothing reaches disk.

Recovery for this leak currently lives **downstream** in
`agent_worker._recover_tool_calls_from_content`, scattered through the agent loop
with complex fence/parity gating. That placement is why it regressed four times
during the 2026-06-29 audit fixes (C1a → C1d → C1e). Each agent worker must call
it; the gating logic is interleaved with agent state it doesn't need.

## Goal

Make the response shape **uniform at the single point every provider passes
through** — `LLMClient._send_request` (the method that builds the `result` dict
from `choice.message`, ~`llm_client.py:731-769`; reached by the public
`LLMClient.chat`) — so that from the boundary downward a turn either has real
`tool_calls` or genuinely made none. Downstream code (agent_worker) stops being
responsible for un-leaking provider output.

Success criteria:
1. A bedrock-style response with empty `tool_calls` + an `<invoke>`/JSON leak in
   `content` comes out of `_send_request` with `result["tool_calls"]` populated.
2. The recovery logic is one pure module, tested in one place; the 8-case edge
   matrix from the audit is preserved with zero behavior change after the move.
3. Full suite (1077 tests) stays green.

Non-goals (YAGNI): no streaming-path change (`_stream` untouched), no litellm
replacement, no native Anthropic SDK (`AnthropicBedrockMantle`) — that was the
higher-risk option explicitly declined. Scope is the non-streaming `_send_request`
boundary only.

## Architecture

**One choke point.** `LLMClient._send_request` is the sole place all providers
(bedrock/openai/g4f/openrouter/local) return a response and map
`choice.message` → `result`. Recovery happens there, once. Downstream consumers
read `result["tool_calls"]` and nothing else about leaks.

**One pure module.** Extract the recovery logic (today split across
`agent_worker.py`) into `app/services/toolcall_recovery.py` — pure functions, no
agent/LLM state. Both the boundary (`llm_client`, primary path) and
`agent_worker` (downstream F2 net) import it. One source of truth, one test
target.

## Components

### New: `app/services/toolcall_recovery.py` (pure)

Public entry point:

```
recover_tool_calls(content: str, valid_names: set[str]) -> list[dict]
```

Runs both parsers and returns native `tool_calls`-shaped dicts:
- JSON form — delimiter-adjacency gate (`_extract_delimiter_adjacent_json` +
  `parse_tool_calls_from_text`).
- XML form — `<invoke>`/`<parameter>` with the line-leading-fence gate and the
  parity-based recover-over-swallow net (`_bare_xml_invoke_blocks`,
  `_scan_invoke_blocks`, `_is_line_leading_fence`).

Moved verbatim from `agent_worker.py` (no logic change): `_extract_delimiter_adjacent_json`,
`_iter_json_object_spans`, `_bare_xml_invoke_blocks`, `_scan_invoke_blocks`,
`_is_line_leading_fence`, the delimiter constants. `parse_xml_invoke_calls` and
`parse_tool_calls_from_text` stay in `prompted_tools.py` and are reused.

`recover_tool_calls` wraps the body in `try/except → return []` — recovery is
best-effort and must never break a valid call.

### Changed: `app/services/llm_client.py` `_send_request` (~lines 752-769)

After mapping `tool_calls` from `choice.message`, before `sanitize_safety_finish`:

```
if not result.get("tool_calls") and content:
    if str(result.get("finish_reason") or "").lower() not in SAFETY_FINISH_REASONS:
        valid = <tool names from the `tools` passed into this call>
        if valid:
            recovered = recover_tool_calls(content, valid)
            if recovered:
                result["tool_calls"] = recovered
                logger.warning("toolcall_recovered_at_boundary", count=len(recovered), ...)
```

- `valid` comes from the `tools` argument already in `_send_request`'s scope (the schemas
  being sent to the model) — so name-validation is exact.
- Safety gate: skip recovery when `finish_reason` is a safety reason, so a
  content-filtered turn never has a tool call executed out of its prose.
- Runs **before** `sanitize_safety_finish` (which only strips on safety reasons,
  which we've already excluded from recovery).

### Changed: `app/orchestration/agent_worker.py`

`_recover_tool_calls_from_content` becomes a one-line delegate to
`toolcall_recovery.recover_tool_calls(content, self._valid_tool_names())`. It is
**kept**, not deleted — the F2 phantom-success guard still calls it on the
tools-off wrap-up `final_content` (a path boundary recovery doesn't cover,
because that synthesis call may carry no `tools`). The moved helper functions are
deleted from `agent_worker.py` and imported from the new module where still
referenced.

## Data flow

```
litellm.acompletion / chat.completions.create
        │
        ▼
choice.message → result{content, tool_calls?, finish_reason, usage}
        │
        ├─ tool_calls present?  ── yes ─▶ use as-is (no recovery)
        │
        └─ no, AND content non-empty AND finish_reason not safety AND tools present
                │
                ▼
        recover_tool_calls(content, valid_names)
                │
                ├─ [] ──▶ result unchanged (genuinely no tool call)
                └─ [calls] ──▶ result["tool_calls"] = calls   (+ boundary log)
        │
        ▼
sanitize_safety_finish(result)  ── unchanged
        │
        ▼
prompted-tools fallback (g4f/pollinations) ── unchanged, still after
        │
        ▼
return result   ── downstream sees uniform shape
```

Note: the existing prompted-tools parse for prompted providers
(`llm_client.py:376-381`) already calls `parse_tool_calls_from_text` when
`prompted_tool_names and not result.get("tool_calls")`. The new boundary recovery
is the **native-provider** analogue (bedrock leaking into prose). They are
complementary; the boundary recovery is gated on `tools` presence and does not
disturb the prompted path.

## Error handling & edge cases

- `recover_tool_calls` never raises (best-effort wrapper).
- No `tools` in the call → no recovery (no `valid_names`).
- Safety `finish_reason` → no recovery (don't execute filtered prose).
- Real `tool_calls` present → recovery skipped entirely (no double-recovery).
- The full 8-case `<invoke>` matrix is preserved (real leak; doc fence
  closed/unclosed; glued-close parity; inline backtick; content-with-fence;
  two-invoke odd-backtick; no-invoke; JSON-adjacency).

## Testing (TDD)

1. **Refactor-is-pure:** after moving helpers to the new module, the existing
   `test_phantom_write_recovery.py` (20 tests) and `test_assignment_loop_hash.py`
   pass unchanged — proves the move altered no behavior. (assignment_loop_hash
   stays in task_similarity; only the phantom-recovery helpers move.)
2. **New `test_toolcall_recovery_boundary.py`:** exercise `_send_request` with a fake
   litellm/openai response object —
   (a) empty `tool_calls` + `<invoke>` in content + tools passed → result has
       recovered `tool_calls`;
   (b) real `tool_calls` present → untouched (no double);
   (c) `finish_reason=content_filter` + invoke in content → no recovery;
   (d) no `tools` argument → no recovery;
   (e) JSON-blob leak adjacent to a delimiter → recovered.
3. **New unit tests** for `toolcall_recovery.recover_tool_calls` covering the
   8-case matrix directly (faster than going through `_send_request`).
4. **Full suite 1077 green** — zero regression.

## Verification

- Reproduce the radar-task XML-invoke leak as a fake `_send_request` response → confirm it
  lands as a `tool_call`.
- One adversarial review pass over the new module (this code regressed repeatedly;
  re-audit after the move).

## Rollback

Pure refactor + one gated boundary block. If the boundary recovery misbehaves,
the gate (`not result.get("tool_calls")`) means it only ever *adds* calls that
would otherwise be lost — it cannot remove or corrupt a real `tool_calls`. Revert
is the single `_send_request` edit; the module move is behavior-neutral.
