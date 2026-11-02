"""
Nexus - Communication Tools.

Register send_message into the global tool registry.
"""

from typing import Any

import structlog

from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


async def handle_send_message(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Gửi tin nhắn (internal messaging)."""
    to = params.get("to_agent", "")
    message = params.get("message", "")

    from app.communication.message_bus import get_message_bus

    bus = await get_message_bus()
    await bus.send_direct(to, {"content": message, "type": "agent_message"})

    return {
        "success": True,
        "output": f"Message sent to {to}",
        "data": {"to": to, "length": len(message)},
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "communication"

registry.register(
    name="send_message",
    toolset=_TOOLSET,
    schema={
        "description": "Send a message to another agent",
        "parameters": {
            "type": "object",
            "properties": {
                "to_agent": {"type": "string", "description": "Target agent ID or name"},
                "message": {"type": "string", "description": "Message content"},
            },
            "required": ["to_agent", "message"],
        },
    },
    handler=handle_send_message,
    is_async=True,
    emoji="💬",
)
