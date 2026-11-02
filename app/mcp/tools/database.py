"""
Nexus - Database Query Tool.

Execute read-only SQL queries against the application database.
Self-registers with the tool registry at import time.

Security: Only SELECT queries are allowed by default.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)

# Dangerous SQL keywords that modify data
_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)

# CRIT-03 FIX: Table allowlist to prevent data exfiltration via prompt injection.
# Only these tables can be queried by the LLM agent. Sensitive tables
# (conversation_messages, agent_memories, security tables) are blocked.
_ALLOWED_TABLES = frozenset({
    "tasks", "agents", "messages", "tools",
    "skills", "experiences", "knowledge_nodes", "knowledge_edges",
    "indexed_documents", "document_chunks", "task_events",
})

# Pattern to extract table names from SQL (FROM/JOIN clauses)
_TABLE_REF_PATTERN = re.compile(
    r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)

MAX_ROWS = 100
MAX_QUERY_LENGTH = 2000


async def _handle_database_query(args: dict[str, Any]) -> str:
    """Execute a read-only SQL query."""
    query = args.get("query", "").strip()
    limit = min(args.get("limit", MAX_ROWS), MAX_ROWS)

    if not query:
        return tool_error("SQL query is required")

    if len(query) > MAX_QUERY_LENGTH:
        return tool_error(f"Query too long (max {MAX_QUERY_LENGTH} chars)")

    # Security: block write operations
    if _WRITE_KEYWORDS.search(query):
        return tool_error(
            "⚠️ Write operations not allowed. Only SELECT queries are permitted. "
            "Detected prohibited keyword in query."
        )

    # Ensure it starts with SELECT (or WITH for CTEs)
    first_word = query.lstrip().split()[0].upper() if query.strip() else ""
    if first_word not in ("SELECT", "WITH", "EXPLAIN", "SHOW"):
        return tool_error(
            f"Only SELECT/WITH/EXPLAIN queries are allowed. Got: {first_word}"
        )

    # CRIT-03 FIX: Verify all referenced tables are in the allowlist.
    # Prevents agent from querying sensitive tables (conversation_messages,
    # agent_memories, security audit logs, etc.) via prompt injection.
    referenced_tables = {
        t.lower() for t in _TABLE_REF_PATTERN.findall(query)
    }
    # Filter out common SQL keywords that may be matched as table names
    referenced_tables -= {"select", "where", "and", "or", "not", "null", "true", "false"}
    blocked_tables = referenced_tables - _ALLOWED_TABLES
    if blocked_tables:
        return tool_error(
            f"⚠️ Access denied to table(s): {', '.join(sorted(blocked_tables))}. "
            f"Allowed tables: {', '.join(sorted(_ALLOWED_TABLES))}"
        )

    # Auto-add LIMIT if not present
    if "LIMIT" not in query.upper():
        query = f"{query.rstrip(';')} LIMIT {limit}"

    try:
        from app.core.dependencies import get_async_session
        from sqlalchemy import text

        async with get_async_session() as session:
            result = await session.execute(text(query))
            columns = list(result.keys()) if result.keys() else []
            rows = [dict(zip(columns, row)) for row in result.fetchall()]

        # Format output as table
        if not rows:
            return tool_result(
                success=True,
                output="Query returned 0 rows.",
                data={"columns": columns, "rows": [], "count": 0},
            )

        # Format as markdown table
        header = "| " + " | ".join(str(c) for c in columns) + " |"
        separator = "| " + " | ".join("---" for _ in columns) + " |"
        body_lines = []
        for row in rows[:limit]:
            cells = " | ".join(str(row.get(c, ""))[:100] for c in columns)
            body_lines.append(f"| {cells} |")

        table = "\n".join([header, separator] + body_lines)
        output = f"📊 Query returned {len(rows)} rows:\n\n{table}"

        if len(rows) >= limit:
            output += f"\n\n*(Limited to {limit} rows)*"

        return tool_result(
            success=True,
            output=output,
            data={"columns": columns, "rows": rows, "count": len(rows)},
        )

    except ImportError:
        return tool_error("Database module not available")
    except Exception as e:
        logger.error("database_query_error", error=str(e), query=query[:200])
        return tool_error(f"Query failed: {e}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="database_query",
    toolset="data",
    schema={
        "description": (
            "Execute a read-only SQL query against the database. "
            "Only SELECT, WITH (CTE), and EXPLAIN queries are allowed. "
            "Results are returned as a formatted table. Max 100 rows."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "SQL SELECT query to execute",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (default: 100)",
                },
            },
            "required": ["query"],
        },
    },
    handler=_handle_database_query,
    is_async=True,
    emoji="📊",
)
