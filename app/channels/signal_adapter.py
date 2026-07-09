"""
MekongAI Nexus - Signal Channel Adapter (Hermes-agent inspired).

Signal messaging via signal-cli-rest-api bridge.

Inspired by: hermes-agent/gateway/platforms/signal.py
Requires: External signal-cli-rest-api instance.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, Optional

import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage

logger = structlog.get_logger(__name__)


class SignalChannel(AbstractChannel):
    """Signal channel via signal-cli-rest-api.

    Configuration:
    - api_url: signal-cli-rest-api URL (e.g. http://localhost:8080)
    - phone_number: Bot's registered phone number
    - poll_interval: seconds between message checks
    """

    def __init__(
        self,
        api_url: str = "http://localhost:8080",
        phone_number: str = "",
        poll_interval: int = 3,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._phone_number = phone_number
        self._poll_interval = poll_interval
        self._handler: Optional[Callable[[InboundMessage], Coroutine]] = None
        self._connected = False
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def channel_type(self) -> str:
        return "signal"

    async def connect(self) -> None:
        if not self._phone_number:
            logger.warning("signal_no_phone_number")
            return
        self._connected = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("signal_channel_connected", phone=self._phone_number)

    async def disconnect(self) -> None:
        self._connected = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("signal_channel_disconnected")

    def set_message_handler(self, handler: Callable) -> None:
        self._handler = handler

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._api_url}/v2/send",
                    json={
                        "message": message.content,
                        "number": self._phone_number,
                        "recipients": [message.channel_id],
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error("signal_send_error", error=str(e))
            return {"error": str(e)}

    async def _poll_loop(self) -> None:
        """Poll signal-cli-rest-api for messages."""
        import httpx

        while self._connected:
            try:
                await asyncio.sleep(self._poll_interval)
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"{self._api_url}/v1/receive/{self._phone_number}",
                    )
                    if resp.status_code == 200:
                        for msg in resp.json():
                            envelope = msg.get("envelope", {})
                            data_msg = envelope.get("dataMessage", {})
                            body = data_msg.get("message", "")
                            sender = envelope.get("source", "")
                            if body and sender and self._handler:
                                inbound = InboundMessage(
                                    channel_type="signal",
                                    channel_id=sender,
                                    sender_id=sender,
                                    content=body,
                                    metadata={"timestamp": envelope.get("timestamp", 0)},
                                )
                                await self._handler(inbound)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("signal_poll_error", error=str(e))
                await asyncio.sleep(10)

    async def health_check(self) -> dict[str, Any]:
        return {
            "channel": "signal",
            "status": "ok" if self._connected else "disconnected",
            "phone": self._phone_number,
        }
