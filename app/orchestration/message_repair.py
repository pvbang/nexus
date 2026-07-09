"""Repair tool_use/tool_result consistency in LLM message history.

Extracted from ``AgentWorker`` (audit C1: shrink the god-object; C3: this
Bedrock/Anthropic-critical repair had zero tests). Pure function over a message
list — no ``self``, no I/O — so it's directly unit-testable.

Bedrock/Anthropic require, after any compression or error recovery:
  1. every tool_use (assistant message with ``tool_calls``) has a matching
     tool_result, and
  2. every tool_result references a tool_use from a prior assistant turn.
A violation in either direction makes the provider reject the request (400).
"""

from __future__ import annotations

from typing import Any

_INTERRUPTED_PLACEHOLDER = "[Tool execution was interrupted or timed out]"


def fix_orphan_tool_use(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconcile tool_use ↔ tool_result so the history is provider-valid.

    - Drops tool_result messages whose tool_call_id has no matching tool_use.
    - Inserts a placeholder tool_result right after the assistant turn for any
      tool_use that never got a result.

    Returns the original list unchanged when there is nothing to fix (no copy,
    same object — callers reassign the return value).
    """
    # Collect tool_call IDs from assistant messages.
    tool_use_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                if tc_id:
                    tool_use_ids.add(tc_id)

    # Collect tool_result IDs.
    tool_result_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id:
                tool_result_ids.add(tc_id)

    orphan_use_ids = tool_use_ids - tool_result_ids
    orphan_result_ids = tool_result_ids - tool_use_ids

    if not orphan_use_ids and not orphan_result_ids:
        return messages

    # Build the fixed message list.
    result: list[dict[str, Any]] = []
    for msg in messages:
        # Remove tool_result messages that have no matching tool_use.
        if msg.get("role") == "tool":
            tc_id = msg.get("tool_call_id", "")
            if tc_id in orphan_result_ids:
                continue  # Drop this orphan result.

        result.append(msg)

        # Add placeholder tool results for orphan tool_uses (right after the
        # assistant turn that issued them).
        if msg.get("role") == "assistant" and orphan_use_ids:
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                if tc_id in orphan_use_ids:
                    result.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": _INTERRUPTED_PLACEHOLDER,
                    })
                    orphan_use_ids.discard(tc_id)

    return result
