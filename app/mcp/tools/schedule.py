"""
Nexus - Schedule Task Tool.

Schedule tasks for future execution using the cron module.
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


async def _handle_schedule_task(args: dict[str, Any]) -> str:
    """Schedule a task for delayed or periodic execution."""
    task_name = args.get("name", "").strip()
    task_type = args.get("type", "once").lower()  # once, interval, cron
    delay_seconds = args.get("delay_seconds", 0)
    interval_seconds = args.get("interval_seconds", 0)
    cron_expression = args.get("cron", "")
    action = args.get("action", "").strip()

    if not task_name:
        return tool_error("Task name is required")
    if not action:
        return tool_error("Task action/description is required")

    try:
        from app.cron.scheduler import get_scheduler

        scheduler = get_scheduler()

        if task_type == "once" and delay_seconds > 0:
            job_id = await scheduler.schedule_once(
                name=task_name,
                delay_seconds=delay_seconds,
                action=action,
            )
        elif task_type == "interval" and interval_seconds > 0:
            job_id = await scheduler.schedule_interval(
                name=task_name,
                interval_seconds=interval_seconds,
                action=action,
            )
        elif task_type == "cron" and cron_expression:
            job_id = await scheduler.schedule_cron(
                name=task_name,
                cron_expression=cron_expression,
                action=action,
            )
        else:
            return tool_error(
                "Invalid schedule config. Use:\n"
                "- type='once' + delay_seconds\n"
                "- type='interval' + interval_seconds\n"
                "- type='cron' + cron expression"
            )

        return tool_result(
            success=True,
            output=f"⏰ Task scheduled: {task_name} (id: {job_id}, type: {task_type})",
            data={"job_id": job_id, "name": task_name, "type": task_type},
        )

    except ImportError:
        return tool_error("Scheduler module not available")
    except Exception as e:
        logger.error("schedule_task_error", error=str(e))
        return tool_error(f"Scheduling failed: {e}")


async def _handle_list_scheduled(args: dict[str, Any]) -> str:
    """List all scheduled tasks."""
    try:
        from app.cron.scheduler import get_scheduler

        scheduler = get_scheduler()
        jobs = await scheduler.list_jobs()

        if not jobs:
            return tool_result(
                success=True,
                output="⏰ No scheduled tasks.",
                data=[],
            )

        lines = [f"⏰ **{len(jobs)} scheduled tasks:**\n"]
        for j in jobs:
            lines.append(
                f"- **{j.get('name', '?')}** (id: {j.get('id', '?')}, "
                f"type: {j.get('type', '?')}, next: {j.get('next_run', '?')})"
            )

        return tool_result(
            success=True,
            output="\n".join(lines),
            data=jobs,
        )
    except ImportError:
        return tool_error("Scheduler module not available")
    except Exception as e:
        return tool_error(f"Failed to list tasks: {e}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="schedule_task",
    toolset="cron",
    schema={
        "description": (
            "Schedule a task for future or periodic execution. "
            "Supports one-time delay, interval, and cron expression schedules."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Task name"},
                "type": {
                    "type": "string",
                    "enum": ["once", "interval", "cron"],
                    "description": "Schedule type",
                },
                "delay_seconds": {"type": "integer", "description": "Delay for 'once' type (seconds)"},
                "interval_seconds": {"type": "integer", "description": "Interval for 'interval' type (seconds)"},
                "cron": {"type": "string", "description": "Cron expression for 'cron' type (e.g., '0 9 * * 1')"},
                "action": {"type": "string", "description": "Description of the task action to execute"},
            },
            "required": ["name", "type", "action"],
        },
    },
    handler=_handle_schedule_task,
    is_async=True,
    emoji="⏰",
)

registry.register(
    name="list_scheduled_tasks",
    toolset="cron",
    schema={
        "description": "List all currently scheduled tasks.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=_handle_list_scheduled,
    is_async=True,
    emoji="📅",
)
