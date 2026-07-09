"""
Execution context for tool handlers.

Uses contextvars to pass session_id, task_id, and event_callback to tools
without changing the tool handler signature (params: dict → result).

Set by orchestrator before tool execution.
Read by tools that need interaction (ask_user, etc.) or task-scoped workspace.
"""

import contextvars
from typing import Any, Callable, Optional

# Session ID of current execution
current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_session_id", default=""
)

# Task ID of current execution — used for per-task workspace directories
current_task_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_task_id", default=""
)

# Parent task ID — set when running as subtask. Subtasks share parent's _work/
# directory so all agents operate on the same codebase without merge conflicts.
current_parent_task_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_parent_task_id", default=""
)

# Agent name of current execution — used for advisory file locking
current_agent_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_agent_name", default=""
)

# Event callback for SSE streaming
current_event_callback: contextvars.ContextVar[Optional[Callable]] = contextvars.ContextVar(
    "current_event_callback", default=None
)

# FileContextTracker instance for current task — used by tool handlers
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.core.file_context_tracker import FileContextTracker

current_file_tracker: contextvars.ContextVar[Optional["FileContextTracker"]] = contextvars.ContextVar(
    "current_file_tracker", default=None
)


def reset_execution_context() -> None:
    """Reset all execution context vars to prevent stale references."""
    current_session_id.set("")
    current_task_id.set("")
    current_parent_task_id.set("")
    current_agent_name.set("")
    current_event_callback.set(None)
    current_file_tracker.set(None)
