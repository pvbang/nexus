"""
MekongAI Nexus - Telegram Channel (OpenClaw-inspired).

Telegram bot integration using python-telegram-bot (or httpx fallback).
"""

import asyncio
from typing import Any, Callable, Awaitable, Optional

import httpx
import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage
from app.config import get_settings

logger = structlog.get_logger(__name__)


class TelegramChannel(AbstractChannel):
    """
    Telegram bot channel via Bot API (polling mode).

    Config: TELEGRAM_BOT_TOKEN in .env
    """

    channel_type = "telegram"

    def __init__(self, enable_polling: Optional[bool] = None) -> None:
        self._token: str = ""
        self._handler: Optional[Callable[[InboundMessage], Awaitable[Optional[str]]]] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._offset: int = 0
        self._running = False
        # None → defer to settings.telegram_polling_enabled at connect() time.
        self._enable_polling = enable_polling

    @staticmethod
    def _resolve_should_poll(explicit: Optional[bool], settings_flag: bool) -> bool:
        """Decide whether to start the background getUpdates loop.

        Telegram's getUpdates is single-consumer; running this loop alongside the
        agent-facing ``telegram_get_updates`` tool causes 409 Conflict and steals
        owner replies. Default is OFF (settings_flag=False) so the tool stays the
        sole consumer. An explicit constructor arg overrides settings.
        """
        if explicit is not None:
            return explicit
        return bool(settings_flag)

    @property
    def _api(self) -> str:
        return f"https://api.telegram.org/bot{self._token}"

    def set_message_handler(self, handler) -> None:
        self._handler = handler

    async def connect(self) -> None:
        settings = get_settings()
        self._token = settings.telegram_bot_token or ""
        if not self._token:
            raise ValueError("TELEGRAM_BOT_TOKEN not configured")
        self._http = httpx.AsyncClient(timeout=60.0)
        # Verify token
        resp = await self._http.get(f"{self._api}/getMe")
        data = resp.json()
        if not data.get("ok"):
            raise ConnectionError(f"Telegram auth failed: {data}")
        bot_name = data["result"].get("username", "unknown")
        logger.info("telegram_connected", bot=bot_name)
        # Start the background long-poll only when explicitly enabled. Otherwise
        # the agent-facing telegram_get_updates tool is the sole getUpdates
        # consumer (no 409 Conflict, owner replies remain visible to the resume
        # detector). The token still powers send().
        should_poll = self._resolve_should_poll(
            self._enable_polling, settings.telegram_polling_enabled
        )
        if should_poll:
            self._running = True
            self._polling_task = asyncio.create_task(self._poll_loop())
            logger.info("telegram_polling_started", bot=bot_name)
        else:
            logger.info(
                "telegram_polling_disabled",
                bot=bot_name,
                reason="single-consumer: telegram_get_updates tool owns getUpdates",
            )

    async def disconnect(self) -> None:
        self._running = False
        if self._polling_task:
            self._polling_task.cancel()
        if self._http:
            await self._http.aclose()

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        if not self._http:
            return {"error": "not connected"}
        payload: dict[str, Any] = {
            "chat_id": message.channel_id,
            "text": message.content[:4096],
            "parse_mode": "Markdown",
        }
        if message.reply_to:
            payload["reply_to_message_id"] = message.reply_to
        resp = await self._http.post(f"{self._api}/sendMessage", json=payload)
        return resp.json()

    async def send_typing(self, channel_id: str) -> None:
        if self._http:
            await self._http.post(f"{self._api}/sendChatAction",
                                  json={"chat_id": channel_id, "action": "typing"})

    async def _poll_loop(self) -> None:
        """Long-polling for updates."""
        while self._running:
            try:
                if not self._http:
                    break
                resp = await self._http.get(
                    f"{self._api}/getUpdates",
                    params={"offset": self._offset, "timeout": 30},
                )
                data = resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(5)
                    continue
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    await self._process_update(update)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("telegram_poll_error")
                await asyncio.sleep(5)

    async def _process_update(self, update: dict) -> None:
        msg = update.get("message")
        if not msg or not msg.get("text"):
            return
        inbound = InboundMessage(
            channel_type="telegram",
            channel_id=str(msg["chat"]["id"]),
            sender_id=str(msg["from"]["id"]),
            sender_name=msg["from"].get("first_name", ""),
            content=msg["text"],
            metadata={"message_id": msg["message_id"], "chat_type": msg["chat"]["type"]},
        )
        if self._handler:
            response = await self._handler(inbound)
            if response:
                await self.send(OutboundMessage(
                    channel_type="telegram",
                    channel_id=inbound.channel_id,
                    content=response,
                    reply_to=str(msg["message_id"]),
                ))

    async def health_check(self) -> dict[str, Any]:
        if not self._http or not self._token:
            return {"channel": "telegram", "status": "disconnected"}
        try:
            resp = await self._http.get(f"{self._api}/getMe")
            return {"channel": "telegram", "status": "ok",
                    "bot": resp.json().get("result", {}).get("username")}
        except Exception as e:
            return {"channel": "telegram", "status": "error", "error": str(e)}
