"""
Nexus - Slack Channel (OpenClaw-inspired).

Slack bot integration using slack_bolt (optional) or httpx fallback.
"""

import asyncio
from typing import Any, Callable, Awaitable, Optional

import httpx
import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage
from app.config import get_settings

logger = structlog.get_logger(__name__)

# Optional slack_bolt import — graceful degradation if not installed
try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_sdk.web.async_client import AsyncWebClient

    SLACK_BOLT_AVAILABLE = True
except ImportError:
    SLACK_BOLT_AVAILABLE = False
    logger.info("slack_bolt_not_installed", hint="pip install slack_bolt for full Slack support")


# ---------------------------------------------------------------------------
# Slack REST API constants
# ---------------------------------------------------------------------------
SLACK_API_BASE = "https://slack.com/api"


class SlackChannel(AbstractChannel):
    """
    Slack bot channel.

    Supports two modes:
    - **Full mode** (slack_bolt installed): Socket Mode events, slash commands,
      Block Kit, interactive buttons, thread replies.
    - **HTTP-only mode** (fallback): send messages via Slack Web API only.

    Config env vars:
        SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_SIGNING_SECRET
    """

    channel_type = "slack"

    def __init__(
        self,
        bot_token: str = "",
        app_token: str = "",
        signing_secret: str = "",
    ) -> None:
        self._bot_token = bot_token
        self._app_token = app_token
        self._signing_secret = signing_secret
        self._handler: Optional[Callable[[InboundMessage], Awaitable[Optional[str]]]] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._running = False

        # slack_bolt objects (only when library is available)
        self._app: Any = None  # AsyncApp
        self._socket_handler: Any = None  # AsyncSocketModeHandler
        self._bot_task: Optional[asyncio.Task] = None
        self._bot_user_id: Optional[str] = None
        self._bot_name: str = ""

    # ------------------------------------------------------------------
    # AbstractChannel interface
    # ------------------------------------------------------------------

    def set_message_handler(self, handler: Any) -> None:
        self._handler = handler

    async def connect(self) -> None:
        settings = get_settings()
        self._bot_token = self._bot_token or settings.slack_bot_token or ""
        self._app_token = self._app_token or settings.slack_app_token or ""
        self._signing_secret = self._signing_secret or settings.slack_signing_secret or ""

        if not self._bot_token:
            raise ValueError("SLACK_BOT_TOKEN not configured")

        self._running = True

        if SLACK_BOLT_AVAILABLE and self._app_token:
            await self._connect_socket_mode()
        else:
            await self._connect_http_only()

    async def disconnect(self) -> None:
        self._running = False
        if self._socket_handler:
            await self._socket_handler.close_async()
        if self._bot_task:
            self._bot_task.cancel()
        if self._http:
            await self._http.aclose()
        logger.info("slack_disconnected")

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        """Send a message to a Slack channel by channel ID."""
        if SLACK_BOLT_AVAILABLE and self._app:
            return await self._send_via_bolt(message)
        return await self._send_via_rest(message)

    async def send_typing(self, channel_id: str) -> None:
        """Slack doesn't have a direct typing indicator API for bots."""
        pass

    async def health_check(self) -> dict[str, Any]:
        if SLACK_BOLT_AVAILABLE and self._app:
            try:
                client: AsyncWebClient = self._app.client
                resp = await client.auth_test()
                if resp.get("ok"):
                    return {
                        "channel": "slack",
                        "status": "ok",
                        "mode": "socket_mode",
                        "bot": resp.get("user", "unknown"),
                        "team": resp.get("team", "unknown"),
                    }
                return {"channel": "slack", "status": "error", "error": resp.get("error")}
            except Exception as e:
                return {"channel": "slack", "status": "error", "error": str(e)}

        if self._http:
            try:
                resp = await self._http.post(
                    f"{SLACK_API_BASE}/auth.test",
                    headers=self._rest_headers,
                )
                data = resp.json()
                if data.get("ok"):
                    return {
                        "channel": "slack",
                        "status": "ok",
                        "mode": "http",
                        "bot": data.get("user", "unknown"),
                        "team": data.get("team", "unknown"),
                    }
                return {"channel": "slack", "status": "error", "error": data.get("error")}
            except Exception as e:
                return {"channel": "slack", "status": "error", "error": str(e)}

        return {"channel": "slack", "status": "disconnected"}

    # ------------------------------------------------------------------
    # Internal: REST API helpers
    # ------------------------------------------------------------------

    @property
    def _rest_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def _send_via_rest(self, message: OutboundMessage) -> dict[str, Any]:
        """Send message via Slack Web API (REST)."""
        if not self._http:
            self._http = httpx.AsyncClient(timeout=30.0)

        payload: dict[str, Any] = {
            "channel": message.channel_id,
        }

        # Block Kit support
        blocks = message.metadata.get("blocks")
        if blocks:
            payload["blocks"] = blocks
            # Blocks need a fallback text
            payload["text"] = message.content[:3000] if message.content else "Message"
        else:
            payload["text"] = message.content[:3000]

        # Thread reply support
        thread_ts = message.metadata.get("thread_ts") or message.reply_to
        if thread_ts:
            payload["thread_ts"] = thread_ts

        # Unfurl settings
        payload["unfurl_links"] = message.metadata.get("unfurl_links", False)
        payload["unfurl_media"] = message.metadata.get("unfurl_media", True)

        try:
            resp = await self._http.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                headers=self._rest_headers,
                json=payload,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.error("slack_send_error", error=data.get("error"))
            return data
        except Exception as e:
            logger.error("slack_send_exception", error=str(e))
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Internal: slack_bolt Socket Mode
    # ------------------------------------------------------------------

    async def _connect_socket_mode(self) -> None:
        """Connect via slack_bolt Socket Mode (full features)."""
        self._app = AsyncApp(
            token=self._bot_token,
            signing_secret=self._signing_secret if self._signing_secret else None,
        )

        self._setup_bolt_handlers()
        self._setup_slash_commands()
        self._setup_interactive_handlers()

        # Identify bot
        try:
            client: AsyncWebClient = self._app.client
            auth_resp = await client.auth_test()
            self._bot_user_id = auth_resp.get("user_id", "")
            self._bot_name = auth_resp.get("user", "")
            logger.info(
                "slack_socket_mode_starting",
                bot=self._bot_name,
                team=auth_resp.get("team", ""),
            )
        except Exception:
            logger.exception("slack_auth_test_failed")

        # Start Socket Mode handler in background
        self._socket_handler = AsyncSocketModeHandler(self._app, self._app_token)
        self._bot_task = asyncio.create_task(self._run_socket_mode())

    async def _run_socket_mode(self) -> None:
        """Run Socket Mode handler in background."""
        try:
            await self._socket_handler.start_async()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("slack_socket_mode_error")

    def _setup_bolt_handlers(self) -> None:
        """Register slack_bolt event handlers."""
        app = self._app

        @app.event("message")
        async def handle_message(event: dict, say: Any) -> None:
            """Handle direct messages and channel messages."""
            # Ignore bot messages and message_changed subtypes
            if event.get("bot_id") or event.get("subtype"):
                return
            # Ignore own messages
            if event.get("user") == self._bot_user_id:
                return

            content = event.get("text", "").strip()
            if not content:
                return

            # Build attachments from files
            attachments = []
            for f in event.get("files", []):
                attachments.append({
                    "filename": f.get("name", ""),
                    "url": f.get("url_private", ""),
                    "size": f.get("size", 0),
                    "content_type": f.get("mimetype", ""),
                })

            inbound = InboundMessage(
                channel_type="slack",
                channel_id=event.get("channel", ""),
                sender_id=event.get("user", ""),
                sender_name=event.get("user", ""),
                content=content,
                attachments=attachments,
                reply_to=event.get("thread_ts", ""),
                metadata={
                    "message_ts": event.get("ts", ""),
                    "thread_ts": event.get("thread_ts", ""),
                    "channel_type": event.get("channel_type", ""),
                    "team": event.get("team", ""),
                },
            )

            if self._handler:
                response = await self._handler(inbound)
                if response:
                    # Reply in thread if original was in thread, or create new thread
                    thread_ts = event.get("thread_ts") or event.get("ts")
                    blocks = _format_response_blocks(response)
                    await say(
                        text=response[:3000],
                        blocks=blocks,
                        thread_ts=thread_ts,
                    )

        @app.event("app_mention")
        async def handle_mention(event: dict, say: Any) -> None:
            """Handle @bot mentions in channels."""
            content = event.get("text", "").strip()

            # Strip bot mention from content
            if self._bot_user_id:
                content = content.replace(f"<@{self._bot_user_id}>", "").strip()

            if not content:
                await say(
                    text="How can I help? Ask me anything!",
                    thread_ts=event.get("ts"),
                )
                return

            inbound = InboundMessage(
                channel_type="slack",
                channel_id=event.get("channel", ""),
                sender_id=event.get("user", ""),
                sender_name=event.get("user", ""),
                content=content,
                metadata={
                    "message_ts": event.get("ts", ""),
                    "thread_ts": event.get("thread_ts", ""),
                    "event_type": "app_mention",
                },
            )

            if self._handler:
                response = await self._handler(inbound)
                if response:
                    thread_ts = event.get("thread_ts") or event.get("ts")
                    blocks = _format_response_blocks(response)
                    await say(
                        text=response[:3000],
                        blocks=blocks,
                        thread_ts=thread_ts,
                    )

    def _setup_slash_commands(self) -> None:
        """Register slash commands."""
        app = self._app

        @app.command("/ask")
        async def handle_ask(ack: Any, command: dict, respond: Any) -> None:
            """Handle /ask slash command."""
            await ack()
            question = command.get("text", "").strip()
            if not question:
                await respond("Please provide a question: `/ask <your question>`")
                return

            inbound = InboundMessage(
                channel_type="slack",
                channel_id=command.get("channel_id", ""),
                sender_id=command.get("user_id", ""),
                sender_name=command.get("user_name", ""),
                content=question,
                metadata={
                    "slash_command": "/ask",
                    "trigger_id": command.get("trigger_id", ""),
                    "response_url": command.get("response_url", ""),
                },
            )

            if self._handler:
                response = await self._handler(inbound)
                if response:
                    blocks = _format_response_blocks(response)
                    await respond(text=response[:3000], blocks=blocks)
                else:
                    await respond("No response generated.")
            else:
                await respond("Handler not configured.")

        @app.command("/status")
        async def handle_status(ack: Any, command: dict, respond: Any) -> None:
            """Handle /status slash command."""
            await ack()
            health = await self.health_check()
            blocks = [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🤖 Nexus Status",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {
                            "type": "mrkdwn",
                            "text": f"*Status:* {'✅ Online' if health.get('status') == 'ok' else '❌ Error'}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Mode:* {health.get('mode', 'unknown')}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Bot:* {health.get('bot', 'unknown')}",
                        },
                        {
                            "type": "mrkdwn",
                            "text": f"*Team:* {health.get('team', 'unknown')}",
                        },
                    ],
                },
            ]
            await respond(text="Nexus Status", blocks=blocks)

    def _setup_interactive_handlers(self) -> None:
        """Register interactive component handlers (buttons, etc.)."""
        app = self._app

        @app.action("confirm_action")
        async def handle_confirm(ack: Any, body: dict, respond: Any) -> None:
            """Handle confirmation button clicks."""
            await ack()
            user = body.get("user", {}).get("id", "")
            action = body.get("actions", [{}])[0]
            value = action.get("value", "")
            await respond(
                text=f"✅ Action confirmed by <@{user}>: {value}",
                replace_original=False,
            )

        @app.action("cancel_action")
        async def handle_cancel(ack: Any, body: dict, respond: Any) -> None:
            """Handle cancel button clicks."""
            await ack()
            await respond(
                text="❌ Action cancelled.",
                replace_original=False,
            )

    async def _send_via_bolt(self, message: OutboundMessage) -> dict[str, Any]:
        """Send message via slack_bolt client."""
        try:
            client: AsyncWebClient = self._app.client
            payload: dict[str, Any] = {
                "channel": message.channel_id,
            }

            # Block Kit support
            blocks = message.metadata.get("blocks")
            if blocks:
                payload["blocks"] = blocks
                payload["text"] = message.content[:3000] if message.content else "Message"
            else:
                payload["text"] = message.content[:3000]

            # Thread reply
            thread_ts = message.metadata.get("thread_ts") or message.reply_to
            if thread_ts:
                payload["thread_ts"] = thread_ts

            resp = await client.chat_postMessage(**payload)
            return {
                "ok": resp.get("ok", False),
                "ts": resp.get("ts", ""),
                "channel": resp.get("channel", ""),
            }
        except Exception as e:
            logger.error("slack_bolt_send_error", error=str(e))
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Internal: HTTP-only fallback mode (no slack_bolt)
    # ------------------------------------------------------------------

    async def _connect_http_only(self) -> None:
        """Connect via REST API only (limited functionality)."""
        self._http = httpx.AsyncClient(timeout=30.0)
        # Verify token
        resp = await self._http.post(
            f"{SLACK_API_BASE}/auth.test",
            headers=self._rest_headers,
        )
        data = resp.json()
        if not data.get("ok"):
            raise ConnectionError(f"Slack auth failed: {data.get('error', 'unknown')}")
        self._bot_user_id = data.get("user_id", "")
        self._bot_name = data.get("user", "")
        logger.info(
            "slack_http_connected",
            bot=self._bot_name,
            team=data.get("team", ""),
            mode="http_only",
        )
        if not SLACK_BOLT_AVAILABLE:
            logger.warning(
                "slack_limited_mode",
                hint="Install slack_bolt for full features: pip install slack_bolt",
            )
        elif not self._app_token:
            logger.warning(
                "slack_no_app_token",
                hint="Set SLACK_APP_TOKEN for Socket Mode (real-time events)",
            )


# ---------------------------------------------------------------------------
# Block Kit formatting utilities
# ---------------------------------------------------------------------------

def _format_response_blocks(text: str) -> list[dict[str, Any]]:
    """
    Format a response string into Slack Block Kit blocks.

    For short messages: simple section block.
    For long messages: split into multiple sections with dividers.
    """
    blocks: list[dict[str, Any]] = []

    if len(text) <= 3000:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text,
            },
        })
    else:
        # Split long messages into chunks
        chunks = _split_text(text, 3000)
        for i, chunk in enumerate(chunks):
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": chunk,
                },
            })
            if i < len(chunks) - 1:
                blocks.append({"type": "divider"})

    return blocks


def _build_confirmation_blocks(
    text: str,
    confirm_value: str = "confirmed",
    cancel_value: str = "cancelled",
) -> list[dict[str, Any]]:
    """Build Block Kit blocks with confirm/cancel buttons."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text,
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Confirm"},
                    "style": "primary",
                    "action_id": "confirm_action",
                    "value": confirm_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "❌ Cancel"},
                    "style": "danger",
                    "action_id": "cancel_action",
                    "value": cancel_value,
                },
            ],
        },
    ]


def _split_text(text: str, limit: int = 3000) -> list[str]:
    """Split text into chunks respecting Slack's block text limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks
