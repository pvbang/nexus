"""
MekongAI Nexus - Message Router (OpenClaw-inspired).

Routes inbound messages from any channel to the correct session/agent.
"""

import structlog
from typing import Any, Callable, Awaitable, Optional

from app.channels.base import InboundMessage, OutboundMessage

logger = structlog.get_logger(__name__)

# Type for the handler that processes routed messages
MessageHandler = Callable[[InboundMessage], Awaitable[Optional[str]]]


class MessageRouter:
    """
    Route inbound channel messages to agent sessions.

    OpenClaw pattern: channel → session → agent routing with allowlists.
    """

    def __init__(self) -> None:
        self._handler: Optional[MessageHandler] = None
        self._allowlists: dict[str, set[str]] = {}  # channel_type -> allowed sender_ids
        self._channel_session_map: dict[str, str] = {}  # "channel:channel_id" -> session_id

    def set_handler(self, handler: MessageHandler) -> None:
        """Set the core message handler (typically the orchestrator)."""
        self._handler = handler

    def set_allowlist(self, channel_type: str, sender_ids: set[str]) -> None:
        """Set allowed senders for a channel. Empty set = allow all."""
        self._allowlists[channel_type] = sender_ids

    def map_channel_to_session(self, channel_type: str, channel_id: str,
                               session_id: str) -> None:
        """Map a channel+id to a specific session."""
        key = f"{channel_type}:{channel_id}"
        self._channel_session_map[key] = session_id

    def resolve_session(self, msg: InboundMessage) -> str:
        """Resolve which session should handle this message."""
        key = f"{msg.channel_type}:{msg.channel_id}"
        return self._channel_session_map.get(key, "main")

    def is_allowed(self, msg: InboundMessage) -> bool:
        """Check if sender is allowed on this channel."""
        allowed = self._allowlists.get(msg.channel_type)
        if allowed is None or len(allowed) == 0:
            return True  # No allowlist = open
        return msg.sender_id in allowed

    async def route(self, msg: InboundMessage) -> Optional[str]:
        """
        Route an inbound message. Returns the response text or None.
        """
        if not self.is_allowed(msg):
            logger.warning("message_blocked", channel=msg.channel_type,
                           sender=msg.sender_id)
            return None

        if self._handler is None:
            logger.error("no_message_handler_set")
            return None

        session_id = self.resolve_session(msg)
        msg.metadata["session_id"] = session_id

        logger.info("message_routed", channel=msg.channel_type,
                     sender=msg.sender_name or msg.sender_id,
                     session=session_id)

        return await self._handler(msg)


_router: Optional[MessageRouter] = None


def get_message_router() -> MessageRouter:
    global _router
    if _router is None:
        _router = MessageRouter()
    return _router
