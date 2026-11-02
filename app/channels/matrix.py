"""
Nexus - Matrix Channel Adapter (Hermes-agent inspired).

Matrix messaging via matrix-nio library. Supports E2EE rooms.

Inspired by: hermes-agent/gateway/platforms/matrix.py
Requires: pip install matrix-nio[e2ee]
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, Optional

import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage

logger = structlog.get_logger(__name__)


class MatrixChannel(AbstractChannel):
    """Matrix channel adapter using matrix-nio.

    Configuration:
    - homeserver: Matrix homeserver URL (e.g. https://matrix.org)
    - user_id: Bot user ID (e.g. @bot:matrix.org)
    - access_token: Bot access token
    - allowed_rooms: Optional list of room IDs to listen in
    """

    def __init__(
        self,
        homeserver: str = "",
        user_id: str = "",
        access_token: str = "",
        allowed_rooms: Optional[list[str]] = None,
    ) -> None:
        self._homeserver = homeserver
        self._user_id = user_id
        self._access_token = access_token
        self._allowed_rooms = set(allowed_rooms or [])
        self._handler: Optional[Callable[[InboundMessage], Coroutine]] = None
        self._client: Any = None
        self._connected = False
        self._sync_task: Optional[asyncio.Task] = None

    @property
    def channel_type(self) -> str:
        return "matrix"

    async def connect(self) -> None:
        if not self._homeserver or not self._access_token:
            logger.warning("matrix_channel_missing_config")
            return

        try:
            from nio import AsyncClient

            self._client = AsyncClient(self._homeserver, self._user_id)
            self._client.access_token = self._access_token

            # Register message callback
            from nio import RoomMessageText
            self._client.add_event_callback(self._on_message, RoomMessageText)

            self._connected = True
            self._sync_task = asyncio.create_task(self._sync_loop())
            logger.info("matrix_channel_connected", homeserver=self._homeserver)

        except ImportError:
            logger.warning("matrix_nio_not_installed", hint="pip install matrix-nio[e2ee]")
        except Exception as e:
            logger.error("matrix_connect_error", error=str(e))

    async def disconnect(self) -> None:
        self._connected = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.close()
        logger.info("matrix_channel_disconnected")

    def set_message_handler(self, handler: Callable) -> None:
        self._handler = handler

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        if not self._client:
            return {"error": "Matrix client not connected"}

        try:
            from nio import RoomSendResponse

            resp = await self._client.room_send(
                room_id=message.channel_id,
                message_type="m.room.message",
                content={
                    "msgtype": "m.text",
                    "body": message.content,
                    "format": "org.matrix.custom.html",
                    "formatted_body": message.content.replace("\n", "<br>"),
                },
            )

            if isinstance(resp, RoomSendResponse):
                return {"status": "sent", "event_id": resp.event_id}
            return {"status": "sent"}

        except Exception as e:
            logger.error("matrix_send_error", error=str(e))
            return {"error": str(e)}

    async def _sync_loop(self) -> None:
        """Matrix sync loop."""
        while self._connected and self._client:
            try:
                await self._client.sync(timeout=30000, full_state=False)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("matrix_sync_error", error=str(e))
                await asyncio.sleep(5)

    async def _on_message(self, room: Any, event: Any) -> None:
        """Handle incoming Matrix messages."""
        if not self._handler:
            return

        # Skip own messages
        if event.sender == self._user_id:
            return

        # Room filter
        if self._allowed_rooms and room.room_id not in self._allowed_rooms:
            return

        inbound = InboundMessage(
            channel_type="matrix",
            channel_id=room.room_id,
            sender_id=event.sender,
            sender_name=room.user_name(event.sender) if hasattr(room, "user_name") else event.sender,
            content=event.body,
            metadata={"event_id": event.event_id},
        )

        try:
            await self._handler(inbound)
        except Exception as e:
            logger.error("matrix_handler_error", error=str(e))

    async def health_check(self) -> dict[str, Any]:
        return {
            "channel": "matrix",
            "status": "ok" if self._connected else "disconnected",
            "homeserver": self._homeserver,
        }
