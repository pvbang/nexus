"""Strip dangling tool_calls when a provider stops for safety reasons.

When an LLM provider halts generation due to a content filter / refusal /
safety policy, any ``tool_calls`` it had begun emitting are typically
truncated or malformed. Executing them is unsafe. This module detects such
finish reasons and removes the partial tool_calls so the agent surfaces the
refusal as a normal message instead of running garbage arguments.

Mirrors deer-flow's ``safety_finish_reason_middleware``.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

SAFETY_FINISH_REASONS = frozenset({
    "content_filter",
    "refusal",
    "safety",
    "blocklist",
    "prohibited_content",
    "spii",
})

_EXPLANATION = (
    "[The model stopped for a safety/content-policy reason before completing "
    "its tool request. The partial tool call was discarded.]"
)


def sanitize_safety_finish(result: dict[str, Any]) -> dict[str, Any]:
    """Drop partial tool_calls produced under a safety finish_reason.

    Mutates and returns ``result``. No-op for normal finish reasons.
    """
    reason = str(result.get("finish_reason") or "").lower()
    if reason not in SAFETY_FINISH_REASONS:
        return result

    result["_safety_stripped"] = True
    if result.get("tool_calls"):
        logger.warning(
            "safety_finish_stripped_tool_calls",
            finish_reason=reason,
            n_tool_calls=len(result["tool_calls"]),
        )
        result["tool_calls"] = []
        if not result.get("content"):
            result["content"] = _EXPLANATION
    return result
