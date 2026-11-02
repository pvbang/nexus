"""
Nexus - Email Diagnostics Tools.

Used to verify IMAP/SMTP connectivity with current env settings without
actually sending an email or downloading full mailbox bodies.
"""

from __future__ import annotations

import smtplib
import ssl
from typing import Any

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


def _email_configured() -> bool:
    s = get_settings()
    return bool(s.email_username) and (bool(s.email_imap_host) or bool(s.email_smtp_host))


async def _email_health_check(params: dict[str, Any], **kwargs: Any) -> str:
    s = get_settings()

    check_imap = bool(params.get("check_imap", True))
    check_smtp = bool(params.get("check_smtp", True))

    result: dict[str, Any] = {
        "imap": {"configured": bool(s.email_imap_host), "ok": False, "error": None},
        "smtp": {"configured": bool(s.email_smtp_host), "ok": False, "error": None},
        "username_set": bool(s.email_username),
        "password_set": bool(s.email_password),
    }

    if not s.email_username:
        return tool_error("EMAIL_USERNAME is not set.")

    if check_imap:
        if not s.email_imap_host or not s.email_password:
            result["imap"]["error"] = "Missing EMAIL_IMAP_HOST or EMAIL_PASSWORD"
        else:
            try:
                import imaplib

                imap = imaplib.IMAP4_SSL(s.email_imap_host, int(s.email_imap_port or 993))
                imap.login(s.email_username, s.email_password)
                imap.select("INBOX")
                imap.logout()
                result["imap"]["ok"] = True
            except Exception as e:
                result["imap"]["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    if check_smtp:
        if not s.email_smtp_host:
            result["smtp"]["error"] = "Missing EMAIL_SMTP_HOST"
        else:
            try:
                host = s.email_smtp_host
                port = int(s.email_smtp_port or 587)
                ctx = ssl.create_default_context()

                # Prefer STARTTLS on typical submission ports.
                with smtplib.SMTP(host, port, timeout=30) as server:
                    server.ehlo()
                    if port in (587, 2525):
                        server.starttls(context=ctx)
                        server.ehlo()
                    if s.email_password:
                        server.login(s.email_username, s.email_password)
                result["smtp"]["ok"] = True
            except Exception as e:
                result["smtp"]["error"] = f"{type(e).__name__}: {str(e)[:200]}"

    lines = ["Email health check:"]
    lines.append(f"- IMAP configured={result['imap']['configured']} ok={result['imap']['ok']} error={result['imap']['error']}")
    lines.append(f"- SMTP configured={result['smtp']['configured']} ok={result['smtp']['ok']} error={result['smtp']['error']}")

    return tool_result(success=True, output="\n".join(lines), data=result)


registry.register(
    name="email_health_check",
    toolset="communication",
    schema={
        "description": "Check IMAP/SMTP connectivity using EMAIL_* env vars (no email is sent).",
        "parameters": {
            "type": "object",
            "properties": {
                "check_imap": {"type": "boolean", "description": "Check IMAP login (default true)", "default": True},
                "check_smtp": {"type": "boolean", "description": "Check SMTP connection/login (default true)", "default": True},
            },
        },
    },
    handler=_email_health_check,
    is_async=True,
    check_fn=_email_configured,
    requires_env=["EMAIL_USERNAME"],
    emoji="*",
    category="communication",
)

