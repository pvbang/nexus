"""
MekongAI Nexus - Subagent Delegation (Hermes-agent inspired).

Spawns child agent instances with isolated context, restricted toolsets,
and their own execution sessions. Supports single-task and batch (parallel).

Each child gets:
  - A fresh conversation (no parent history)
  - Restricted toolset (configurable)
  - Focused system prompt from delegated goal + context
  - Independent token budget

The parent only sees the delegation call and summary result.

Inspired by: hermes-agent/tools/delegate_tool.py
"""

import asyncio
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import structlog

from app.services.subagent_status import SubagentStatus

logger = structlog.get_logger(__name__)

# Tools that children must never have access to
DELEGATE_BLOCKED_TOOLS = frozenset([
    "delegate_task",   # no recursive delegation
    "send_message",    # no cross-platform side effects
])

MAX_DEPTH = 3  # parent (0) -> child (1) -> grandchild (2) -> great-grandchild rejected (3)
DEFAULT_MAX_ITERATIONS = 50
DEFAULT_MAX_CONCURRENT = 5

SUBAGENT_PRODUCTION_MANDATE = (
    "\n\n## ⚡ PRODUCTION STANDARD\n"
    "You are a subagent assigned a specific task. Your output MUST:\n"
    "- Achieve production quality — no demo/toy code\n"
    "- Use the appropriate modern tech stack\n"
    "- Include proper error handling & validation\n"
    "- If writing code: code must be complete, runnable, tested\n"
    "- If building a project: deploy locally to verify\n"
    "- Never use placeholder/TODO — implement fully\n"
    "\n## SHARED WORKSPACE — you are NOT working alone\n"
    "The `_work/` directory is SHARED between all agents (parent and subtasks).\n"
    "Files you see may have been created by other agents. You CAN AND SHOULD:\n"
    "- READ other agents' files to understand context\n"
    "- EDIT/MODIFY files created by other agents if your task requires it\n"
    "- BUILD UPON existing work — do not create duplicate/parallel files\n"
    "- Treat workspace as your team's shared codebase\n"
    "\n## ⚠️ ANTI-PATTERN: RE-ANALYSIS TRAP\n"
    "If your CONTEXT section above already describes file contents, data structures, or\n"
    "transformations needed — DO NOT re-read those same files. Trust the context.\n"
    "Your FIRST tool call should PRODUCE output (write_file, execute_code), not read_file\n"
    "on content already described in your brief. Re-reading known content = WASTED TURN.\n"
    "\n## OUTPUT LANGUAGE\n"
    "Your thinking and all output MUST be in the SAME LANGUAGE as the user's original request.\n"
    "If user wrote in Vietnamese → think and respond in Vietnamese. If English → English.\n"
    "\n## CODING QUALITY\n"
    "- Fix root causes, not symptoms. No hardcoding.\n"
    "- Clean code — if 200 lines could be 50, rewrite.\n"
    "- Surgical changes — only touch what your task requires.\n"
    "- Verify your output works before declaring done.\n"
)


def build_child_system_prompt(
    goal: str,
    context: Optional[str] = None,
    workspace_path: Optional[str] = None,
) -> str:
    """Build a focused system prompt for a child agent."""
    parts = [
        "You are a focused subagent working on a specific delegated task.",
        "",
        f"YOUR TASK:\n{goal}",
    ]
    if context and context.strip():
        parts.append(f"\nCONTEXT:\n{context}")
    if workspace_path:
        parts.append(f"\nWORKSPACE PATH:\n{workspace_path}")
    parts.append(
        "\nComplete this task using tools available to you. "
        "When finished, provide a clear summary of:\n"
        "- What you did\n"
        "- What you found or accomplished\n"
        "- Any files created or modified\n"
        "- Any issues encountered\n\n"
        "Be thorough but concise — your response is returned to the parent agent."
    )
    parts.append(SUBAGENT_PRODUCTION_MANDATE)
    return "\n".join(parts)


class DelegationResult:
    """Result of a delegated task."""

    def __init__(
        self,
        task_index: int,
        goal: str,
        status: str = "pending",
        summary: str = "",
        error: Optional[str] = None,
        duration_seconds: float = 0.0,
        api_calls: int = 0,
        tokens: Optional[dict] = None,
        tool_trace: Optional[list] = None,
    ):
        self.task_index = task_index
        self.goal = goal
        if not SubagentStatus.is_valid(status):
            logger.warning("subagent_invalid_status", given=status)
            status = SubagentStatus.ERROR
        self.status = status
        self.summary = summary
        self.error = error
        self.duration_seconds = duration_seconds
        self.api_calls = api_calls
        self.tokens = tokens or {"input": 0, "output": 0}
        self.tool_trace = tool_trace or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_index": self.task_index,
            "goal": self.goal[:100],
            "status": self.status,
            "summary": self.summary,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "api_calls": self.api_calls,
            "tokens": self.tokens,
            "tool_trace": self.tool_trace,
        }


class SubagentDelegator:
    """
    Manages subagent delegation with isolated contexts.

    Supports:
    - Single task delegation
    - Batch (parallel) delegation
    - Depth limiting
    - Tool restriction
    - Progress tracking
    """

    def __init__(
        self,
        orchestrator_fn=None,
        max_depth: int = MAX_DEPTH,
        max_concurrent: int = DEFAULT_MAX_CONCURRENT,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ):
        self.orchestrator_fn = orchestrator_fn
        self.max_depth = max_depth
        self.max_concurrent = max_concurrent
        self.max_iterations = max_iterations
        self._active_children: list[str] = []
        self._current_depth = 0

    async def delegate_single(
        self,
        goal: str,
        context: Optional[str] = None,
        toolsets: Optional[list[str]] = None,
        model: Optional[str] = None,
        max_iterations: Optional[int] = None,
        depth: int = 0,
    ) -> DelegationResult:
        """Delegate a single task to a subagent."""
        if depth >= self.max_depth:
            return DelegationResult(
                task_index=0,
                goal=goal,
                status="rejected",
                error=f"Delegation depth limit reached ({self.max_depth})",
            )

        start_time = time.monotonic()
        child_id = str(uuid.uuid4())[:8]
        self._active_children.append(child_id)

        logger.info(
            "subagent_started",
            child_id=child_id,
            depth=depth,
            goal=goal[:100],
        )

        try:
            if self.orchestrator_fn:
                # Use the provided orchestrator function to run the subtask
                result = await self.orchestrator_fn(
                    message=goal,
                    context=context,
                    is_subagent=True,
                    max_iterations=max_iterations or self.max_iterations,
                )
                duration = round(time.monotonic() - start_time, 2)

                return DelegationResult(
                    task_index=0,
                    goal=goal,
                    status="completed" if result.get("success") else "failed",
                    summary=result.get("response", ""),
                    error=result.get("error"),
                    duration_seconds=duration,
                    api_calls=result.get("api_calls", 0),
                )
            else:
                # Fallback: no orchestrator function configured
                logger.warning(
                    "subagent_no_orchestrator_fn",
                    child_id=child_id,
                    goal=goal[:100],
                    message="orchestrator_fn is None — delegation cannot proceed. "
                            "Returning no_executor status.",
                )
                return DelegationResult(
                    task_index=0,
                    goal=goal,
                    status="no_executor",
                    error="No orchestrator function configured for subagent delegation",
                    summary=(
                        "Delegation failed: no orchestrator_fn was provided to "
                        "SubagentDelegator. The parent agent must configure an "
                        "orchestrator function before delegating tasks."
                    ),
                )

        except Exception as exc:
            duration = round(time.monotonic() - start_time, 2)
            logger.exception("subagent_failed", child_id=child_id, error=str(exc))
            return DelegationResult(
                task_index=0,
                goal=goal,
                status="error",
                error=str(exc),
                duration_seconds=duration,
            )
        finally:
            if child_id in self._active_children:
                self._active_children.remove(child_id)
            logger.info("subagent_completed", child_id=child_id)

    async def delegate_batch(
        self,
        tasks: list[dict[str, Any]],
        depth: int = 0,
    ) -> list[DelegationResult]:
        """Delegate multiple tasks in parallel."""
        if depth >= self.max_depth:
            return [
                DelegationResult(
                    task_index=i,
                    goal=t.get("goal", ""),
                    status="rejected",
                    error="Depth limit reached",
                )
                for i, t in enumerate(tasks)
            ]

        if len(tasks) > self.max_concurrent:
            return [
                DelegationResult(
                    task_index=0,
                    goal="batch",
                    status="rejected",
                    error=f"Too many tasks: {len(tasks)} > max {self.max_concurrent}",
                )
            ]

        overall_start = time.monotonic()
        results = []

        # Run tasks concurrently
        coros = []
        for i, task in enumerate(tasks):
            coro = self.delegate_single(
                goal=task.get("goal", ""),
                context=task.get("context"),
                toolsets=task.get("toolsets"),
                depth=depth,
            )
            coros.append(coro)

        completed = await asyncio.gather(*coros, return_exceptions=True)

        for i, result in enumerate(completed):
            if isinstance(result, Exception):
                results.append(DelegationResult(
                    task_index=i,
                    goal=tasks[i].get("goal", ""),
                    status="error",
                    error=str(result),
                ))
            else:
                result.task_index = i
                results.append(result)

        total_duration = round(time.monotonic() - overall_start, 2)
        logger.info(
            "batch_delegation_complete",
            task_count=len(tasks),
            total_duration=total_duration,
        )

        return results

    def get_active_children(self) -> list[str]:
        return list(self._active_children)


# Delegation tool schema (OpenAI function calling format)
DELEGATE_TASK_SCHEMA = {
    "name": "delegate_task",
    "description": (
        "Spawn one or more subagents to work on tasks in isolated contexts. "
        "Each subagent gets its own conversation and toolset. "
        "Only the final summary is returned.\n\n"
        "TWO MODES:\n"
        "1. Single task: provide 'goal' (+ optional context, toolsets)\n"
        "2. Batch (parallel): provide 'tasks' array\n\n"
        "WHEN TO USE:\n"
        "- Reasoning-heavy subtasks (debugging, code review, research)\n"
        "- Tasks that would flood your context with intermediate data\n"
        "- Parallel independent workstreams\n\n"
        "IMPORTANT:\n"
        "- Subagents have NO memory of your conversation\n"
        "- Pass all relevant info via the 'context' field"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "What the subagent should accomplish.",
            },
            "context": {
                "type": "string",
                "description": "Background information: file paths, errors, constraints.",
            },
            "toolsets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Toolsets to enable. Default: ['terminal', 'file', 'web']",
            },
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "goal": {"type": "string"},
                        "context": {"type": "string"},
                        "toolsets": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["goal"],
                },
                "description": "Batch mode: tasks to run in parallel (max 3).",
            },
            "max_iterations": {
                "type": "integer",
                "description": "Max tool-calling turns per subagent (default: 50).",
            },
        },
        "required": [],
    },
}
