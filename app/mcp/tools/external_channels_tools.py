"""
Nexus - External World Messaging Tools.

Goal: bridge the "local/academic" gap by letting agents communicate with the
outside world via common channels (Telegram/Slack/Discord/WhatsApp/Signal).

Design:
- Prefer direct HTTP APIs with httpx so these tools work even when the FastAPI
  channel gateway isn't running/connected.
- Keep inputs simple and consistent across channels.
- Fail with actionable configuration hints (env vars).
"""

from __future__ import annotations

from typing import Any, Optional

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


def _get_default_target(channel: str) -> str:
    s = get_settings()
    c = (channel or "").lower().strip()
    if c == "telegram":
        return (s.telegram_default_chat_id or "").strip()
    if c == "slack":
        return (s.slack_default_channel or "").strip()
    if c == "discord":
        return (s.discord_default_channel_id or "").strip()
    if c == "whatsapp":
        return (s.whatsapp_default_to or "").strip()
    if c == "signal":
        return (s.signal_default_recipient or "").strip()
    return ""


def _available() -> bool:
    # Always available: tools validate per-channel config at runtime.
    return True


async def _world_connectivity_status(params: dict[str, Any], **kwargs: Any) -> str:
    """Report which external connectors appear configured (without leaking secrets)."""
    s = get_settings()

    def _set(v: Optional[str]) -> bool:
        return bool((v or "").strip())

    status = {
        "telegram": {
            "token": _set(s.telegram_bot_token),
            "default_chat_id": _set(s.telegram_default_chat_id),
        },
        "slack": {
            "bot_token": _set(s.slack_bot_token),
            "default_channel": _set(s.slack_default_channel),
            "socket_mode_app_token": _set(s.slack_app_token),
        },
        "discord": {
            "bot_token": _set(s.discord_bot_token),
            "default_channel_id": _set(s.discord_default_channel_id),
        },
        "whatsapp": {
            "enabled": bool(s.whatsapp_enabled),
            "mode": (s.whatsapp_mode or "bridge").strip(),
            "bridge_url": _set(s.whatsapp_api_url),
            "cloud_phone_number_id": _set(s.whatsapp_phone_number_id),
            "cloud_access_token": _set(s.whatsapp_api_token),
            "default_to": _set(s.whatsapp_default_to),
        },
        "signal": {
            "api_url": _set(s.signal_api_url),
            "phone_number": _set(s.signal_phone_number),
            "default_recipient": _set(s.signal_default_recipient),
        },
        "email": {
            "imap_host": _set(s.email_imap_host),
            "smtp_host": _set(s.email_smtp_host),
            "username": _set(s.email_username),
            "from_address": _set(s.email_from_address),
        },
        "webhook": {
            "secret": _set(s.webhook_secret),
        },
    }

    lines = ["External connectivity status (configured flags only):"]
    for ch, info in status.items():
        parts = []
        for k, v in info.items():
            parts.append(f"{k}={v}")
        lines.append(f"- {ch}: " + ", ".join(parts))

    return tool_result(success=True, output="\n".join(lines), data=status)


async def _create_public_inbox(params: dict[str, Any], **kwargs: Any) -> str:
    """Create a new public inbox token and return an endpoint path (and full URL if available)."""
    label = (params.get("label") or "").strip()
    conversation_id = (params.get("conversation_id") or "").strip() or None

    from app.core.public_inbox import create_public_inbox

    inbox = create_public_inbox(label=label, conversation_id=conversation_id)

    # Build best-effort URL: uses settings.external_base_url if set.
    from app.config import get_settings
    s = get_settings()
    path = f"/api/v1/public/inbox/{inbox.token}"
    full_url = (s.external_base_url or "").rstrip("/") + path if (s.external_base_url or "").strip() else ""

    out_lines = [
        "Public inbox created (token-in-URL). Treat this link as a secret.",
        f"- Path: {path}",
        "- GET shows a simple UI form; POST accepts JSON: {\"message\":\"...\"}",
        f"- Conversation: {inbox.conversation_id}",
    ]
    if full_url:
        out_lines.append(f"- URL: {full_url}")
    else:
        out_lines.append("- URL: (set EXTERNAL_BASE_URL or use expose_port() and combine manually)")

    return tool_result(
        success=True,
        output="\n".join(out_lines),
        data={
            "token": inbox.token,
            "path": path,
            "url": full_url or None,
            "conversation_id": inbox.conversation_id,
            "label": inbox.label,
        },
    )


async def _send_external_message(params: dict[str, Any], **kwargs: Any) -> str:
    """
    Send a message to an external channel.

    Channels supported (HTTP mode):
    - telegram: Bot API
    - slack: chat.postMessage (Web API)
    - discord: create message (REST)
    - whatsapp: Baileys bridge (/send) or Meta Cloud API
    - signal: signal-cli-rest-api (/v2/send)
    """
    s = get_settings()

    channel = (params.get("channel") or "").strip().lower()
    text = (params.get("text") or "").strip()
    to = (params.get("to") or "").strip() or _get_default_target(channel)

    if not channel:
        return tool_error("Parameter 'channel' is required (telegram|slack|discord|whatsapp|signal).")
    if not text:
        return tool_error("Parameter 'text' is required.")

    # Limit to prevent accidental huge spam payloads.
    if len(text) > 8000:
        text = text[:8000] + "\n...[truncated]"

    try:
        import httpx
    except Exception:
        return tool_error("Missing dependency: httpx. Install it to use external messaging tools.")

    # ----------------------------
    # Telegram
    # ----------------------------
    if channel == "telegram":
        token = (s.telegram_bot_token or "").strip()
        if not token:
            return tool_error("Telegram not configured. Set TELEGRAM_BOT_TOKEN.")
        if not to:
            return tool_error("Telegram target missing. Provide 'to' (chat_id) or set TELEGRAM_DEFAULT_CHAT_ID.")

        parse_mode = (params.get("parse_mode") or "").strip()  # MarkdownV2, HTML
        disable_preview = bool(params.get("disable_web_page_preview", False))

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": to, "text": text, "disable_web_page_preview": disable_preview}
        if parse_mode:
            payload["parse_mode"] = parse_mode

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if not data.get("ok"):
            return tool_error(f"Telegram API returned ok=false: {data}")

        return tool_result(
            success=True,
            output=f"Sent Telegram message to chat_id={to} ({len(text)} chars).",
            data={"channel": "telegram", "to": to, "text_length": len(text)},
        )

    # ----------------------------
    # Slack
    # ----------------------------
    if channel == "slack":
        token = (s.slack_bot_token or "").strip()
        if not token:
            return tool_error("Slack not configured. Set SLACK_BOT_TOKEN.")
        if not to:
            return tool_error("Slack target missing. Provide 'to' (channel ID) or set SLACK_DEFAULT_CHANNEL.")

        thread_ts = (params.get("thread_ts") or "").strip()

        url = "https://slack.com/api/chat.postMessage"
        payload: dict[str, Any] = {"channel": to, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
                json=payload,
            )
            data = resp.json()

        if not data.get("ok"):
            return tool_error(f"Slack API error: {data.get('error') or data}")

        return tool_result(
            success=True,
            output=f"Sent Slack message to channel={to}.",
            data={"channel": "slack", "to": to, "ts": data.get("ts"), "thread_ts": data.get("message", {}).get("thread_ts")},
        )

    # ----------------------------
    # Discord
    # ----------------------------
    if channel == "discord":
        token = (s.discord_bot_token or "").strip()
        if not token:
            return tool_error("Discord not configured. Set DISCORD_BOT_TOKEN.")
        if not to:
            return tool_error("Discord target missing. Provide 'to' (channel ID) or set DISCORD_DEFAULT_CHANNEL_ID.")

        url = f"https://discord.com/api/v10/channels/{to}/messages"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bot {token}", "Content-Type": "application/json"},
                json={"content": text},
            )
            if resp.status_code >= 400:
                return tool_error(f"Discord API error: HTTP {resp.status_code} {resp.text[:500]}")
            data = resp.json()

        return tool_result(
            success=True,
            output=f"Sent Discord message to channel_id={to}.",
            data={"channel": "discord", "to": to, "message_id": data.get('id')},
        )

    # ----------------------------
    # WhatsApp
    # ----------------------------
    if channel == "whatsapp":
        if not bool(s.whatsapp_enabled):
            return tool_error("WhatsApp is disabled. Set WHATSAPP_ENABLED=true to opt-in.")
        if not to:
            return tool_error("WhatsApp target missing. Provide 'to' or set WHATSAPP_DEFAULT_TO.")

        mode = (s.whatsapp_mode or "bridge").strip().lower()
        if mode == "cloud":
            if not s.whatsapp_phone_number_id or not s.whatsapp_api_token:
                return tool_error("WhatsApp Cloud API not configured. Set WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_API_TOKEN.")
            url = f"https://graph.facebook.com/v18.0/{s.whatsapp_phone_number_id}/messages"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {s.whatsapp_api_token}"},
                    json={
                        "messaging_product": "whatsapp",
                        "to": to,
                        "type": "text",
                        "text": {"body": text},
                    },
                )
                if resp.status_code >= 400:
                    return tool_error(f"WhatsApp Cloud API error: HTTP {resp.status_code} {resp.text[:500]}")
                data = resp.json()
        else:
            bridge = (s.whatsapp_api_url or "").strip().rstrip("/")
            if not bridge:
                return tool_error("WhatsApp bridge not configured. Set WHATSAPP_API_URL.")
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(f"{bridge}/send", json={"to": to, "text": text})
                if resp.status_code >= 400:
                    return tool_error(f"WhatsApp bridge error: HTTP {resp.status_code} {resp.text[:500]}")
                data = resp.json()

        return tool_result(
            success=True,
            output=f"Sent WhatsApp message to={to} (mode={mode}).",
            data={"channel": "whatsapp", "to": to, "mode": mode, "response": data},
        )

    # ----------------------------
    # Signal
    # ----------------------------
    if channel == "signal":
        api_url = (s.signal_api_url or "").strip().rstrip("/")
        phone = (s.signal_phone_number or "").strip()
        if not api_url:
            return tool_error("Signal not configured. Set SIGNAL_API_URL (signal-cli-rest-api).")
        if not phone:
            return tool_error("Signal not configured. Set SIGNAL_PHONE_NUMBER (bot number).")
        if not to:
            return tool_error("Signal target missing. Provide 'to' or set SIGNAL_DEFAULT_RECIPIENT.")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_url}/v2/send",
                json={"message": text, "number": phone, "recipients": [to]},
            )
            if resp.status_code >= 400:
                return tool_error(f"Signal API error: HTTP {resp.status_code} {resp.text[:500]}")
            data = resp.json()

        return tool_result(
            success=True,
            output=f"Sent Signal message to={to}.",
            data={"channel": "signal", "to": to, "response": data},
        )

    return tool_error(f"Unsupported channel '{channel}'. Supported: telegram|slack|discord|whatsapp|signal.")


async def _telegram_get_updates(params: dict[str, Any], **kwargs: Any) -> str:
    """Poll Telegram getUpdates for inbound messages (useful for tool-driven workflows)."""
    s = get_settings()
    token = (s.telegram_bot_token or "").strip()
    if not token:
        return tool_error("Telegram not configured. Set TELEGRAM_BOT_TOKEN.")

    offset = params.get("offset", None)
    limit = int(params.get("limit") or 20)
    timeout = int(params.get("timeout") or 10)
    allowed_updates = params.get("allowed_updates")  # optional list

    if limit <= 0:
        return tool_error("limit must be > 0")
    limit = min(limit, 100)
    timeout = max(0, min(timeout, 50))

    url = f"https://api.telegram.org/bot{token}/getUpdates"
    query: dict[str, Any] = {"limit": limit, "timeout": timeout}
    if offset is not None:
        try:
            query["offset"] = int(offset)
        except Exception:
            return tool_error("offset must be an integer")
    if isinstance(allowed_updates, list) and allowed_updates:
        query["allowed_updates"] = allowed_updates

    try:
        import httpx
        async with httpx.AsyncClient(timeout=float(timeout) + 10.0) as client:
            resp = await client.get(url, params=query)
            resp.raise_for_status()
            data = resp.json()
        if not data.get("ok"):
            return tool_error(f"Telegram API returned ok=false: {data}")

        updates = data.get("result", []) or []
        simplified: list[dict[str, Any]] = []
        for u in updates:
            msg = u.get("message") or u.get("edited_message") or {}
            text = msg.get("text") or ""
            chat = msg.get("chat") or {}
            frm = msg.get("from") or {}
            simplified.append(
                {
                    "update_id": u.get("update_id"),
                    "chat_id": str(chat.get("id", "")),
                    "chat_type": chat.get("type", ""),
                    "from_id": str(frm.get("id", "")),
                    "from_name": frm.get("first_name", ""),
                    "message_id": msg.get("message_id"),
                    "date": msg.get("date"),
                    "text": text,
                }
            )

        return tool_result(
            success=True,
            output=f"Fetched {len(simplified)} Telegram update(s).",
            data={"count": len(simplified), "updates": simplified},
        )
    except Exception as e:
        logger.error("telegram_get_updates_failed", error=str(e)[:200])
        return tool_error(f"Failed to fetch Telegram updates: {e}")


registry.register(
    name="send_external_message",
    toolset="communication",
    schema={
        "description": (
            "Send a message to an external channel (Telegram/Slack/Discord/WhatsApp/Signal) "
            "via direct HTTP APIs. Use this to contact customers/users or operational channels."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "enum": ["telegram", "slack", "discord", "whatsapp", "signal"],
                    "description": "External channel type.",
                },
                "to": {
                    "type": "string",
                    "description": (
                        "Target identifier. For telegram: chat_id. For slack: channel ID. "
                        "For discord: channel ID. For whatsapp/signal: recipient address/number. "
                        "If omitted, a channel-specific default env var is used."
                    ),
                },
                "text": {"type": "string", "description": "Message text"},
                "parse_mode": {
                    "type": "string",
                    "description": "Telegram only. Optional: MarkdownV2, HTML",
                },
                "disable_web_page_preview": {
                    "type": "boolean",
                    "description": "Telegram only. Disable link previews (default false).",
                    "default": False,
                },
                "thread_ts": {
                    "type": "string",
                    "description": "Slack only. Send as a thread reply to the given thread timestamp.",
                },
            },
            "required": ["channel", "text"],
        },
    },
    handler=_send_external_message,
    is_async=True,
    check_fn=_available,
    emoji="*",
    category="communication",
)


registry.register(
    name="telegram_get_updates",
    toolset="communication",
    schema={
        "description": "Poll Telegram Bot API getUpdates and return simplified inbound messages.",
        "parameters": {
            "type": "object",
            "properties": {
                "offset": {"type": "integer", "description": "Update offset (optional)"},
                "limit": {"type": "integer", "description": "Max updates (default 20, max 100)"},
                "timeout": {"type": "integer", "description": "Long poll timeout seconds (default 10, max 50)"},
                "allowed_updates": {"type": "array", "items": {"type": "string"}, "description": "Optional allowed_updates list"},
            },
        },
    },
    handler=_telegram_get_updates,
    is_async=True,
    emoji="*",
    category="communication",
)


registry.register(
    name="world_connectivity_status",
    toolset="communication",
    schema={
        "description": "Show which external connectors appear configured (without exposing secrets).",
        "parameters": {"type": "object", "properties": {}},
    },
    handler=_world_connectivity_status,
    is_async=True,
    emoji="*",
    category="communication",
)


registry.register(
    name="create_public_inbox",
    toolset="communication",
    schema={
        "description": (
            "Create a public inbox token. External users can POST to /api/v1/public/inbox/{token} "
            "without JWT. Use expose_port() to get a public base URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Optional label for humans"},
                "conversation_id": {"type": "string", "description": "Optional fixed conversation_id (otherwise auto)"},
            },
        },
    },
    handler=_create_public_inbox,
    is_async=True,
    emoji="*",
    category="communication",
)
