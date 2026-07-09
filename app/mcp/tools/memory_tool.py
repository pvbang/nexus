"""
MekongAI Nexus - Memory Tool (Hermes-agent inspired).

Persistent memory management tool that lets agents read/write
MEMORY.md (agent notes) and USER.md (user profile) files.

Self-registers with the tool registry at import time.

Inspired by: hermes-agent/tools/memory_tool.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)

# Memory file locations
_MEMORY_DIR = Path("workspace_data/.agent_memory")
_MEMORY_DIR.mkdir(parents=True, exist_ok=True)

_MEMORY_FILE = _MEMORY_DIR / "MEMORY.md"
_USER_FILE = _MEMORY_DIR / "USER.md"

# Character limits (hermes-agent: ~800 tokens memory, ~500 tokens user)
_MEMORY_MAX_CHARS = 3200   # ~800 tokens
_USER_MAX_CHARS = 2000     # ~500 tokens


def _ensure_files() -> None:
    """Create memory files if they don't exist."""
    if not _MEMORY_FILE.exists():
        _MEMORY_FILE.write_text(
            "# Agent Memory\n\n"
            "<!-- Persistent notes about environment, conventions, and important facts -->\n\n",
            encoding="utf-8",
        )
    if not _USER_FILE.exists():
        _USER_FILE.write_text(
            "# User Profile\n\n"
            "<!-- Preferences, communication style, and important user context -->\n\n",
            encoding="utf-8",
        )


# ── Tool handlers ────────────────────────────────────────────────────────

def _memory_read(args: dict[str, Any]) -> str:
    """Read persistent memory or user profile."""
    section = args.get("section", "memory").lower()
    _ensure_files()

    if section == "user":
        content = _USER_FILE.read_text(encoding="utf-8")
        return tool_result(
            success=True,
            output=f"📝 **User Profile** ({len(content)} chars):\n\n{content}",
            data={"section": "user", "content": content, "chars": len(content)},
        )

    content = _MEMORY_FILE.read_text(encoding="utf-8")
    return tool_result(
        success=True,
        output=f"🧠 **Agent Memory** ({len(content)} chars):\n\n{content}",
        data={"section": "memory", "content": content, "chars": len(content)},
    )


def _memory_write(args: dict[str, Any]) -> str:
    """Write/append to persistent memory or user profile."""
    section = args.get("section", "memory").lower()
    content = args.get("content", "").strip()
    mode = args.get("mode", "append").lower()  # append | replace

    if not content:
        return tool_error("Content is required")

    _ensure_files()

    if section == "user":
        target = _USER_FILE
        max_chars = _USER_MAX_CHARS
        label = "User Profile"
    else:
        target = _MEMORY_FILE
        max_chars = _MEMORY_MAX_CHARS
        label = "Agent Memory"

    try:
        from app.core.atomic_writes import atomic_write_text
    except ImportError:
        atomic_write_text = None

    if mode == "replace":
        new_content = content
    else:
        existing = target.read_text(encoding="utf-8")
        new_content = existing.rstrip() + "\n\n" + content

    # Enforce character limit
    if len(new_content) > max_chars:
        overflow = len(new_content) - max_chars
        return tool_error(
            f"{label} would exceed limit ({len(new_content)}/{max_chars} chars). "
            f"Please prune {overflow} chars first, or use mode='replace' with shorter content."
        )

    # Write atomically if available
    if atomic_write_text is not None:
        atomic_write_text(target, new_content)
    else:
        target.write_text(new_content, encoding="utf-8")

    return tool_result(
        success=True,
        output=f"✅ {label} updated ({len(new_content)}/{max_chars} chars used).",
        data={"section": section, "chars": len(new_content), "max": max_chars},
    )


def _memory_search(args: dict[str, Any]) -> str:
    """Search across both memory files."""
    query = args.get("query", "").strip().lower()
    if not query:
        return tool_error("Search query is required")

    _ensure_files()
    results = []

    for label, path in [("Agent Memory", _MEMORY_FILE), ("User Profile", _USER_FILE)]:
        content = path.read_text(encoding="utf-8")
        if query in content.lower():
            # Find matching lines
            matching_lines = [
                line.strip()
                for line in content.split("\n")
                if query in line.lower() and line.strip()
            ]
            if matching_lines:
                results.append({
                    "section": label,
                    "matches": matching_lines[:5],
                })

    if not results:
        return tool_result(
            success=True,
            output=f"No matches found for '{query}' in memory files.",
            data=[],
        )

    lines = [f"🔍 **Memory search for '{query}':**\n"]
    for r in results:
        lines.append(f"\n**{r['section']}:**")
        for m in r["matches"]:
            lines.append(f"  - {m}")

    return tool_result(
        success=True,
        output="\n".join(lines),
        data=results,
    )


# ── Self-registration ────────────────────────────────────────────────────

registry.register(
    name="memory_read",
    toolset="memory",
    schema={
        "description": "Read persistent agent memory (MEMORY.md) or user profile (USER.md). Memory persists across sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["memory", "user"],
                    "description": "Which file to read: 'memory' (agent notes) or 'user' (user profile)",
                    "default": "memory",
                },
            },
        },
    },
    handler=_memory_read,
    emoji="🧠",
)

registry.register(
    name="memory_write",
    toolset="memory",
    schema={
        "description": "Write to persistent agent memory or user profile. Use to remember important facts, preferences, and conventions.",
        "parameters": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["memory", "user"],
                    "description": "Which file to write: 'memory' or 'user'",
                    "default": "memory",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write/append",
                },
                "mode": {
                    "type": "string",
                    "enum": ["append", "replace"],
                    "description": "Write mode: 'append' adds to existing, 'replace' overwrites",
                    "default": "append",
                },
            },
            "required": ["content"],
        },
    },
    handler=_memory_write,
    emoji="💾",
)

registry.register(
    name="memory_search",
    toolset="memory",
    schema={
        "description": "Search across agent memory and user profile files for specific information.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term to find in memory files"},
            },
            "required": ["query"],
        },
    },
    handler=_memory_search,
    emoji="🔍",
)
