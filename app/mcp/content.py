"""Render MCP ``tools/call`` results into LLM-readable text.

The MCP spec allows a tool result's ``content`` array to contain text, image,
audio, and embedded-resource blocks, plus an optional top-level
``structuredContent`` object. The legacy extractor kept only text blocks and
silently dropped everything else — breaking vision/file MCP servers.

This extractor preserves text verbatim, renders binary blocks as compact
placeholders (mime + byte count, never inlining raw base64 into the prompt
stream), surfaces resource URIs/text, and appends structured content as JSON.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _approx_bytes(data: str) -> int:
    """Best-effort decoded length of a base64 string, for placeholders."""
    if not isinstance(data, str) or not data:
        return 0
    try:
        return len(base64.b64decode(data, validate=False))
    except Exception:
        return len(data)


def _render_block(block: Any) -> str:
    if not isinstance(block, dict):
        return str(block)
    btype = block.get("type")
    if btype == "text":
        return str(block.get("text", ""))
    if btype in ("image", "audio"):
        mime = block.get("mimeType", "application/octet-stream")
        n = _approx_bytes(block.get("data", ""))
        return f"[{btype}: {mime}, {n} bytes]"
    if btype == "resource":
        res = block.get("resource", {})
        if isinstance(res, dict):
            if res.get("text"):
                return str(res["text"])
            uri = res.get("uri", "unknown")
            mime = res.get("mimeType", "")
            suffix = f", {mime}" if mime else ""
            return f"[resource: {uri}{suffix}]"
        return "[resource]"
    # Unknown block type — surface its JSON so nothing is silently lost.
    return f"[{btype or 'unknown'}: {json.dumps(block, ensure_ascii=False)[:300]}]"


def extract_tool_result(result: Any) -> str:
    """Convert an MCP tool-call result into readable text. Never raises."""
    try:
        if not isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False) if result is not None else ""

        parts: list[str] = []
        content = result.get("content")
        if isinstance(content, list):
            for block in content:
                rendered = _render_block(block)
                if rendered:
                    parts.append(rendered)

        structured = result.get("structuredContent")
        if structured is not None:
            parts.append(json.dumps(structured, ensure_ascii=False, indent=2))

        if parts:
            return "\n".join(parts)

        # No recognizable content — fall back to the whole object.
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("mcp_content_extract_failed", error=str(exc))
        return json.dumps({"error": f"content extraction failed: {exc}"})
