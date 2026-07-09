"""
MekongAI Nexus - Telegram Tools.

Real-world integration: send Telegram messages via a bot token.
This helps bridge the "local/academic" gap for operational tasks.

Env/config:
- TELEGRAM_BOT_TOKEN: required
- TELEGRAM_DEFAULT_CHAT_ID: optional default chat_id
"""

from __future__ import annotations

import os
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


def _telegram_available() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())


async def _send_telegram_message(params: dict[str, Any], **kwargs: Any) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return tool_error("Telegram not configured. Set TELEGRAM_BOT_TOKEN.")

    text = (params.get("text") or "").strip()
    if not text:
        return tool_error("Parameter 'text' is required.")

    chat_id = (params.get("chat_id") or os.environ.get("TELEGRAM_DEFAULT_CHAT_ID") or "").strip()
    if not chat_id:
        return tool_error("Parameter 'chat_id' is required (or set TELEGRAM_DEFAULT_CHAT_ID).")

    parse_mode = (params.get("parse_mode") or "").strip()  # e.g. MarkdownV2, HTML
    disable_preview = bool(params.get("disable_web_page_preview", False))

    try:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if not data.get("ok"):
            return tool_error(f"Telegram API returned ok=false: {data}")

        logger.info("telegram_message_sent", chat_id=str(chat_id))
        return tool_result(
            success=True,
            output=f"Telegram message sent to chat_id={chat_id} ({len(text)} chars).",
            data={"chat_id": chat_id, "text_length": len(text)},
        )
    except Exception as e:
        logger.error("telegram_send_failed", error=str(e)[:200])
        return tool_error(f"Failed to send Telegram message: {e}")


registry.register(
    name="send_telegram_message",
    toolset="communication",
    schema={
        "description": "Send a Telegram message via bot token (TELEGRAM_BOT_TOKEN).",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Target chat_id. If omitted, TELEGRAM_DEFAULT_CHAT_ID is used.",
                },
                "text": {"type": "string", "description": "Message text"},
                "parse_mode": {
                    "type": "string",
                    "description": "Optional parse_mode: MarkdownV2, HTML",
                },
                "disable_web_page_preview": {
                    "type": "boolean",
                    "description": "Disable link previews (default false)",
                    "default": False,
                },
            },
            "required": ["text"],
        },
    },
    handler=_send_telegram_message,
    is_async=True,
    check_fn=_telegram_available,
    requires_env=["TELEGRAM_BOT_TOKEN"],
    emoji="@",
    category="communication",
)
