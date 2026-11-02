"""
Nexus - Email Inbox Tool (IMAP).

Fetch recent emails from an IMAP inbox so agents can "receive information from email"
and draft replies (send_email tool covers outbound).

Config from Settings (app/config.py):
- EMAIL_IMAP_HOST, EMAIL_USERNAME, EMAIL_PASSWORD
- EMAIL_IMAP_PORT (default 993)
"""

from __future__ import annotations

import email
import imaplib
from email.header import decode_header
from typing import Any

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


def _imap_available() -> bool:
    s = get_settings()
    return bool(s.email_imap_host and s.email_username and s.email_password)


def _decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for chunk, enc in decode_header(value):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", errors="replace"))
            except Exception:
                parts.append(chunk.decode("utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts).strip()


def _extract_text_snippet(msg: email.message.Message, max_chars: int = 2000) -> str:
    # Prefer text/plain, fall back to text/html (stripped minimally).
    text = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
                break
        if not text:
            for part in msg.walk():
                ctype = (part.get_content_type() or "").lower()
                disp = (part.get("Content-Disposition") or "").lower()
                if "attachment" in disp:
                    continue
                if ctype == "text/html":
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    text = payload.decode(charset, errors="replace")
                    break
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        text = payload.decode(charset, errors="replace")

    text = (text or "").strip().replace("\r\n", "\n")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[truncated]"
    return text


async def _fetch_emails(params: dict[str, Any], **kwargs: Any) -> str:
    s = get_settings()

    folder = (params.get("folder") or "INBOX").strip()
    query = (params.get("query") or "ALL").strip()  # IMAP SEARCH syntax
    limit = int(params.get("limit") or 10)
    include_body = bool(params.get("include_body", False))

    if limit <= 0:
        return tool_error("limit must be > 0")
    limit = min(limit, 50)

    if not _imap_available():
        return tool_error(
            "Email inbox not configured. Set EMAIL_IMAP_HOST, EMAIL_USERNAME, EMAIL_PASSWORD."
        )

    try:
        host = s.email_imap_host
        port = int(s.email_imap_port or 993)
        user = s.email_username
        password = s.email_password

        imap = imaplib.IMAP4_SSL(host, port)
        imap.login(user, password)
        imap.select(folder)

        typ, data = imap.search(None, query)
        if typ != "OK":
            imap.logout()
            return tool_error(f"IMAP search failed: {typ} {data}")

        ids = (data[0] or b"").split()
        ids = ids[-limit:]  # newest last

        results: list[dict[str, Any]] = []
        for mid in reversed(ids):  # newest first
            typ, msg_data = imap.fetch(mid, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _decode_mime_header(msg.get("Subject"))
            from_ = _decode_mime_header(msg.get("From"))
            date = _decode_mime_header(msg.get("Date"))

            item: dict[str, Any] = {
                "id": mid.decode("ascii", errors="replace"),
                "from": from_,
                "subject": subject,
                "date": date,
            }

            if include_body:
                item["body"] = _extract_text_snippet(msg)

            results.append(item)

        imap.logout()

        logger.info("imap_fetched", folder=folder, count=len(results))
        return tool_result(
            success=True,
            output=f"Fetched {len(results)} message(s) from {folder}.",
            data={"folder": folder, "query": query, "limit": limit, "messages": results},
        )
    except Exception as e:
        logger.error("imap_fetch_failed", error=str(e)[:200])
        return tool_error(f"Failed to fetch emails: {e}")


registry.register(
    name="fetch_emails",
    toolset="communication",
    schema={
        "description": "Fetch recent emails from an IMAP inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "folder": {"type": "string", "description": "IMAP folder (default INBOX)"},
                "query": {
                    "type": "string",
                    "description": "IMAP SEARCH query (default ALL). Example: 'UNSEEN' or 'FROM \"foo@bar.com\"'.",
                },
                "limit": {"type": "integer", "description": "Max messages to fetch (default 10, max 50)"},
                "include_body": {"type": "boolean", "description": "Include a body snippet (default false)"},
            },
        },
    },
    handler=_fetch_emails,
    is_async=True,
    check_fn=_imap_available,
    requires_env=["EMAIL_IMAP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD"],
    emoji="@",
    category="communication",
)
