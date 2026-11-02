"""
Nexus - WhatsApp Channel Adapter (Hermes-agent inspired).

WhatsApp integration via Baileys bridge (Node.js) or Cloud API.
Supports text messages, media, and document attachments.

Inspired by: hermes-agent/gateway/platforms/whatsapp.py
Requires: External Baileys bridge process or Meta Cloud API credentials.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Coroutine, Optional

import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage

logger = structlog.get_logger(__name__)


class WhatsAppChannel(AbstractChannel):
    """WhatsApp channel via REST bridge (Baileys or Cloud API).

    Configuration:
    - bridge_url: URL of the Baileys bridge (e.g. http://localhost:3001)
    - phone_number_id: Meta Cloud API phone number ID
    - access_token: Meta Cloud API access token
    - mode: "bridge" (Baileys) or "cloud" (Meta Cloud API)
    """

    def __init__(
        self,
        bridge_url: str = "http://localhost:3001",
        phone_number_id: str = "",
        access_token: str = "",
        mode: str = "bridge",
    ) -> None:
        self._bridge_url = bridge_url.rstrip("/")
        self._phone_number_id = phone_number_id
        self._access_token = access_token
        self._mode = mode
        self._handler: Optional[Callable[[InboundMessage], Coroutine]] = None
        self._connected = False
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def channel_type(self) -> str:
        return "whatsapp"

    async def connect(self) -> None:
        self._connected = True
        if self._mode == "bridge":
            self._poll_task = asyncio.create_task(self._poll_bridge())
        logger.info("whatsapp_channel_connected", mode=self._mode)

    async def disconnect(self) -> None:
        self._connected = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("whatsapp_channel_disconnected")

    def set_message_handler(self, handler: Callable) -> None:
        self._handler = handler

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        """Send message via WhatsApp."""
        try:
            import httpx

            if self._mode == "cloud":
                return await self._send_cloud(message)

            # Baileys bridge mode
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._bridge_url}/send",
                    json={
                        "to": message.channel_id,
                        "text": message.content,
                    },
                )
                resp.raise_for_status()
                return resp.json()

        except Exception as e:
            logger.error("whatsapp_send_error", error=str(e))
            return {"error": str(e)}

    async def _send_cloud(self, message: OutboundMessage) -> dict[str, Any]:
        """Send via Meta Cloud API."""
        import httpx

        url = f"https://graph.facebook.com/v18.0/{self._phone_number_id}/messages"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": message.channel_id,
                    "type": "text",
                    "text": {"body": message.content},
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def _poll_bridge(self) -> None:
        """Poll Baileys bridge for new messages."""
        import httpx

        while self._connected:
            try:
                await asyncio.sleep(2)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"{self._bridge_url}/messages")
                    if resp.status_code == 200:
                        messages = resp.json()
                        for msg in messages:
                            if self._handler:
                                inbound = InboundMessage(
                                    channel_type="whatsapp",
                                    channel_id=msg.get("from", ""),
                                    sender_id=msg.get("from", ""),
                                    sender_name=msg.get("pushName", ""),
                                    content=msg.get("body", ""),
                                )
                                await self._handler(inbound)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("whatsapp_poll_error", error=str(e))
                await asyncio.sleep(10)

    async def handle_webhook(self, payload: dict[str, Any]) -> Optional[str]:
        """Handle Meta Cloud API webhook callback."""
        if not self._handler:
            return None

        try:
            entries = payload.get("entry", [])
            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    for msg in messages:
                        inbound = InboundMessage(
                            channel_type="whatsapp",
                            channel_id=msg.get("from", ""),
                            sender_id=msg.get("from", ""),
                            content=msg.get("text", {}).get("body", ""),
                            metadata={"wa_id": msg.get("id", "")},
                        )
                        result = await self._handler(inbound)
                        return result
        except Exception as e:
            logger.error("whatsapp_webhook_error", error=str(e))
        return None

    async def health_check(self) -> dict[str, Any]:
        return {
            "channel": "whatsapp",
            "status": "ok" if self._connected else "disconnected",
            "mode": self._mode,
        }
