"""
Nexus - Email Tool.

Send emails via SMTP. Configuration from settings (app/config.py).
Self-registers with the tool registry at import time.
"""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


def _email_available() -> bool:
    """Check if SMTP is configured."""
    settings = get_settings()
    return bool(settings.email_smtp_host and settings.email_username)


async def _handle_send_email(args: dict[str, Any]) -> str:
    """Send an email via SMTP."""
    to = args.get("to", "").strip()
    subject = args.get("subject", "").strip()
    body = args.get("body", "").strip()
    html = args.get("html", False)

    if not to:
        return tool_error("Recipient email ('to') is required")
    if not subject:
        return tool_error("Subject is required")
    if not body:
        return tool_error("Body is required")

    settings = get_settings()
    smtp_host = settings.email_smtp_host
    smtp_port = settings.email_smtp_port
    smtp_user = settings.email_username
    smtp_pass = settings.email_password
    from_addr = settings.email_from_address or smtp_user

    if not smtp_host or not smtp_user:
        return tool_error(
            "Email not configured. Set EMAIL_SMTP_HOST, EMAIL_USERNAME, "
            "EMAIL_PASSWORD in environment."
        )

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject

        content_type = "html" if html else "plain"
        msg.attach(MIMEText(body, content_type, "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            if smtp_port in (587, 2525):
                server.starttls()
                server.ehlo()
            if smtp_pass:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info("email_sent", to=to, subject=subject[:50])
        return tool_result(
            success=True,
            output=f"📧 Email sent to {to}\nSubject: {subject}",
            data={"to": to, "subject": subject, "from": from_addr},
        )
    except Exception as e:
        logger.error("email_send_failed", to=to, error=str(e))
        return tool_error(f"Failed to send email: {e}")


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="send_email",
    toolset="communication",
    schema={
        "description": "Send an email via SMTP. Supports plain text and HTML body.",
        "parameters": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body (plain text or HTML)"},
                "html": {"type": "boolean", "description": "Set true if body is HTML (default: false)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    handler=_handle_send_email,
    is_async=True,
    check_fn=_email_available,
    requires_env=["EMAIL_SMTP_HOST", "EMAIL_USERNAME"],
    emoji="📧",
)
