"""
Nexus - Complete Task Tool.

DEPRECATED: This tool is a no-op. Pipeline orchestration manages task completion
automatically via NexusOrchestrator evaluation. Agents should NOT call this tool.
The tool is kept for reference but NOT registered with the tool registry.

Signals task completion from within the agent execution loop.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.tool_registry import tool_result

logger = structlog.get_logger(__name__)


async def _handle_complete_task(args: dict[str, Any]) -> str:
    """Mark the current task as complete with a final result.

    DEPRECATED: Pipeline manages completion automatically.
    """
    result_text = args.get("result", "").strip()

    if not result_text:
        return tool_result(
            success=False,
            output="Error: 'result' parameter is required.",
        )

    logger.info("task_completed_via_tool", result_length=len(result_text))

    return tool_result(
        success=True,
        output=result_text,
        data={"completed": True},
    )


# ---------------------------------------------------------------------------
# Registration — DISABLED
# Pipeline orchestration manages task completion automatically.
# Keeping this tool registered caused agents to short-circuit the evaluation
# loop by calling complete_task prematurely.
# ---------------------------------------------------------------------------

# registry.register(
#     name="complete_task",
#     toolset="intelligence",
#     category="intelligence",
#     description=(
#         "Signal that the task is complete and deliver the final result to the user. "
#         "Call this when you have finished the task and have a definitive answer."
#     ),
#     schema={
#         "type": "object",
#         "properties": {
#             "result": {
#                 "type": "string",
#                 "description": (
#                     "The final result to deliver to the user. Must be a complete, "
#                     "definitive answer — not a question or offer for further help."
#                 ),
#             },
#         },
#         "required": ["result"],
#     },
#     handler=_handle_complete_task,
#     is_async=True,
#     emoji="✅",
# )
