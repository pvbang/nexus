"""
Nexus - Notification Tool.

Push notifications to users through their active channel.
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


async def _handle_notify_user(args: dict[str, Any]) -> str:
    """Send notification to the current user via their active channel."""
    message = args.get("message", "").strip()
    title = args.get("title", "").strip()
    urgency = args.get("urgency", "normal").lower()

    if not message:
        return tool_error("Notification message is required")

    notification = {
        "type": "notification",
        "title": title or "Nexus",
        "message": message,
        "urgency": urgency,
    }

    # Try WebSocket broadcast first (frontend)
    try:
        from app.api.ws import broadcast_notification
        await broadcast_notification(notification)
        logger.info("notification_sent_ws", title=title[:50], urgency=urgency)
        return tool_result(
            success=True,
            output=f"🔔 Notification sent: {title or 'Notification'}\n{message}",
            data=notification,
        )
    except (ImportError, Exception):
        pass

    # Fallback: try message bus
    try:
        from app.communication.message_bus import get_message_bus
        bus = await get_message_bus()
        await bus.broadcast(notification)
        logger.info("notification_sent_bus", title=title[:50])
        return tool_result(
            success=True,
            output=f"🔔 Notification broadcast: {title or 'Notification'}\n{message}",
            data=notification,
        )
    except (ImportError, Exception) as e:
        logger.warning("notification_fallback", error=str(e))

    # Last resort: just return the notification as output
    return tool_result(
        success=True,
        output=f"🔔 **{title or 'Notification'}** ({urgency})\n\n{message}",
        data=notification,
    )


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="notify_user",
    toolset="communication",
    schema={
        "description": (
            "Send a notification to the user. Use for task completion alerts, "
            "long-running process updates, or important information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Notification message"},
                "title": {"type": "string", "description": "Notification title (optional)"},
                "urgency": {
                    "type": "string",
                    "enum": ["low", "normal", "high"],
                    "description": "Urgency level (default: normal)",
                },
            },
            "required": ["message"],
        },
    },
    handler=_handle_notify_user,
    is_async=True,
    emoji="🔔",
)
