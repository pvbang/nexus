"""
MekongAI Nexus - Session Search Tool (Hermes-agent inspired).

Lets agents search through past conversation history using
the existing PostgreSQL FTS (full-text search) backend.

Self-registers with the tool registry at import time.

Inspired by: hermes-agent/tools/session_search_tool.py
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


# ── Tool handlers ────────────────────────────────────────────────────────

async def _session_search(args: dict[str, Any]) -> str:
    """Search past conversations for relevant context."""
    query = args.get("query", "").strip()
    limit = args.get("limit", 5)

    if not query:
        return tool_error("Search query is required")

    try:
        from app.core.session_search import search_sessions
        results = await search_sessions(query, limit=limit)

        if not results:
            return tool_result(
                success=True,
                output=f"No past conversations found matching: '{query}'",
                data=[],
            )

        lines = [f"🔍 **Found {len(results)} matches for '{query}':**\n"]
        for i, r in enumerate(results, 1):
            session_id = r.get("session_id", "")[:8]
            content = r.get("content", "")[:300]
            role = r.get("role", "unknown")
            timestamp = r.get("created_at", "")

            lines.append(
                f"**{i}.** [{role}] (session: {session_id}... | {timestamp})\n"
                f"   {content}\n"
            )

        return tool_result(
            success=True,
            output="\n".join(lines),
            data=results,
        )
    except ImportError:
        return tool_error("Session search module not available")
    except Exception as e:
        logger.error("session_search_tool_error", error=str(e))
        return tool_error(f"Search failed: {e}")


async def _session_list_recent(args: dict[str, Any]) -> str:
    """List recent conversation sessions."""
    limit = args.get("limit", 10)

    try:
        from app.core.session_search import list_recent_sessions
        sessions = await list_recent_sessions(limit=limit)

        if not sessions:
            return tool_result(
                success=True,
                output="No recent sessions found.",
                data=[],
            )

        lines = [f"📜 **{len(sessions)} recent sessions:**\n"]
        for s in sessions:
            sid = s.get("id", "")[:8]
            title = s.get("title", "Untitled")
            msg_count = s.get("message_count", 0)
            created = s.get("created_at", "")

            lines.append(f"- **{title}** (session: {sid}... | {msg_count} msgs | {created})")

        return tool_result(
            success=True,
            output="\n".join(lines),
            data=sessions,
        )
    except ImportError:
        return tool_error("Session search module not available")
    except Exception as e:
        logger.error("session_list_tool_error", error=str(e))
        return tool_error(f"List failed: {e}")


# ── Self-registration ────────────────────────────────────────────────────

registry.register(
    name="session_search",
    toolset="session_search",
    schema={
        "description": "Search past conversation history for relevant context, decisions, or information from previous sessions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to find in past conversations"},
                "limit": {"type": "integer", "description": "Maximum results to return", "default": 5},
            },
            "required": ["query"],
        },
    },
    handler=_session_search,
    is_async=True,
    emoji="🔍",
)

registry.register(
    name="session_list_recent",
    toolset="session_search",
    schema={
        "description": "List recent conversation sessions to browse history.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum sessions to list", "default": 10},
            },
        },
    },
    handler=_session_list_recent,
    is_async=True,
    emoji="📜",
)
