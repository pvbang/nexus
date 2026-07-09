"""
MekongAI Nexus - Todo Tool (Hermes-agent inspired).

In-memory task planning tool that lets agents organize their work.
Each session gets its own task list. Tasks are NOT persisted across
sessions — they exist purely as an agent planning aid.

Self-registers with the tool registry at import time.

Inspired by: hermes-agent/tools/todo_tool.py
"""

from __future__ import annotations

import json
import threading
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)

# Thread-safe per-session todo storage
_todo_store: dict[str, list[dict[str, Any]]] = {}
_lock = threading.Lock()

_SESSION_KEY = "__default__"


def _get_todos(session_id: str | None = None) -> list[dict[str, Any]]:
    """Get the todo list for a session."""
    key = session_id or _SESSION_KEY
    with _lock:
        if key not in _todo_store:
            _todo_store[key] = []
        return _todo_store[key]


# ── Tool handlers ────────────────────────────────────────────────────────

def _todo_add(args: dict[str, Any]) -> str:
    """Add a task to the todo list."""
    task = args.get("task", "").strip()
    if not task:
        return tool_error("Task description is required")

    priority = args.get("priority", 5)
    session_id = args.get("session_id")
    todos = _get_todos(session_id)

    entry = {
        "index": len(todos) + 1,
        "task": task,
        "priority": priority,
        "completed": False,
    }
    todos.append(entry)

    return tool_result(
        success=True,
        output=f"✅ Added task #{entry['index']}: {task} (priority: {priority})",
        data=entry,
    )


def _todo_list(args: dict[str, Any]) -> str:
    """List all tasks."""
    session_id = args.get("session_id")
    todos = _get_todos(session_id)

    if not todos:
        return tool_result(
            success=True,
            output="📋 Todo list is empty. Use todo_add to add tasks.",
            data=[],
        )

    lines = ["📋 **Todo List:**\n"]
    for t in sorted(todos, key=lambda x: (-x["priority"], x["index"])):
        status = "✅" if t["completed"] else "⬜"
        lines.append(f"{status} #{t['index']} [P{t['priority']}] {t['task']}")

    pending = sum(1 for t in todos if not t["completed"])
    done = sum(1 for t in todos if t["completed"])
    lines.append(f"\n**{pending} pending, {done} completed**")

    return tool_result(
        success=True,
        output="\n".join(lines),
        data=todos,
    )


def _todo_complete(args: dict[str, Any]) -> str:
    """Mark a task as completed."""
    index = args.get("index")
    if index is None:
        return tool_error("Task index is required")

    session_id = args.get("session_id")
    todos = _get_todos(session_id)

    for t in todos:
        if t["index"] == index:
            t["completed"] = True
            return tool_result(
                success=True,
                output=f"✅ Completed task #{index}: {t['task']}",
                data=t,
            )

    return tool_error(f"Task #{index} not found")


def _todo_clear(args: dict[str, Any]) -> str:
    """Clear all tasks."""
    session_id = args.get("session_id")
    key = session_id or _SESSION_KEY
    with _lock:
        count = len(_todo_store.get(key, []))
        _todo_store[key] = []

    return tool_result(
        success=True,
        output=f"🗑️ Cleared {count} tasks.",
    )


# ── Self-registration ────────────────────────────────────────────────────

registry.register(
    name="todo_add",
    toolset="todo",
    schema={
        "description": "Add a task to the planning todo list for tracking work progress.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Task description"},
                "priority": {"type": "integer", "description": "Priority 1-10 (10 = highest)", "default": 5},
            },
            "required": ["task"],
        },
    },
    handler=_todo_add,
    emoji="📝",
)

registry.register(
    name="todo_list",
    toolset="todo",
    schema={
        "description": "List all tasks in the planning todo list with their status.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=_todo_list,
    emoji="📋",
)

registry.register(
    name="todo_complete",
    toolset="todo",
    schema={
        "description": "Mark a task as completed by its index number.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "Task index number to complete"},
            },
            "required": ["index"],
        },
    },
    handler=_todo_complete,
    emoji="✅",
)

registry.register(
    name="todo_clear",
    toolset="todo",
    schema={
        "description": "Clear all tasks from the todo list.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=_todo_clear,
    emoji="🗑️",
)
