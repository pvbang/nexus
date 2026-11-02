"""
Nexus - Email Channel Adapter (Hermes-agent inspired).

IMAP polling for inbound + SMTP for outbound email communication.
Supports document attachments and HTML formatting.

Inspired by: hermes-agent/gateway/platforms/email.py
"""

from __future__ import annotations

import asyncio
import email
import imaplib
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable, Coroutine, Optional

import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage

logger = structlog.get_logger(__name__)


class EmailChannel(AbstractChannel):
    """Email channel — IMAP polling + SMTP sending.

    Configuration via constructor or environment:
    - imap_host, imap_port, imap_user, imap_password
    - smtp_host, smtp_port, smtp_user, smtp_password
    - poll_interval: seconds between IMAP checks (default: 30)
    """

    def __init__(
        self,
        imap_host: str = "",
        imap_port: int = 993,
        imap_user: str = "",
        imap_password: str = "",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        from_address: str = "",
        poll_interval: int = 30,
    ) -> None:
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._imap_user = imap_user
        self._imap_password = imap_password
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user or imap_user
        self._smtp_password = smtp_password or imap_password
        self._from_address = from_address or imap_user
        self._poll_interval = poll_interval
        self._handler: Optional[Callable[[InboundMessage], Coroutine]] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._connected = False
        self._last_uid = "0"

    @property
    def channel_type(self) -> str:
        return "email"

    async def connect(self) -> None:
        if not self._imap_host:
            logger.warning("email_channel_no_imap_host")
            return
        self._connected = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("email_channel_connected", imap=self._imap_host)

    async def disconnect(self) -> None:
        self._connected = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("email_channel_disconnected")

    def set_message_handler(self, handler: Callable) -> None:
        self._handler = handler

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        """Send email via SMTP."""
        if not self._smtp_host:
            return {"error": "SMTP not configured"}

        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = self._from_address
            msg["To"] = message.channel_id  # channel_id = recipient email
            msg["Subject"] = message.metadata.get("subject", "Nexus")

            # Plain text + HTML
            text_part = MIMEText(message.content, "plain", "utf-8")
            html_content = message.content.replace("\n", "<br>\n")
            html_part = MIMEText(f"<html><body>{html_content}</body></html>", "html", "utf-8")
            msg.attach(text_part)
            msg.attach(html_part)

            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._send_smtp, msg)

            logger.info("email_sent", to=message.channel_id)
            return {"status": "sent", "to": message.channel_id}

        except Exception as e:
            logger.error("email_send_error", error=str(e))
            return {"error": str(e)}

    def _send_smtp(self, msg: MIMEMultipart) -> None:
        """Synchronous SMTP send (runs in executor)."""
        with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
            server.ehlo()
            if self._smtp_port != 25:
                server.starttls()
            if self._smtp_user:
                server.login(self._smtp_user, self._smtp_password)
            server.send_message(msg)

    async def _poll_loop(self) -> None:
        """Background IMAP polling loop."""
        while self._connected:
            try:
                await asyncio.sleep(self._poll_interval)
                if not self._connected:
                    break
                await self._check_inbox()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("email_poll_error", error=str(e))
                await asyncio.sleep(60)  # Back off on error

    async def _check_inbox(self) -> None:
        """Check IMAP inbox for new messages."""
        loop = asyncio.get_event_loop()
        messages = await loop.run_in_executor(None, self._fetch_new_emails)

        for msg_data in messages:
            # Skip automated notifications (GitHub PR emails, bulk/list mail) —
            # they must NOT spawn tasks. Header-based, vendor-neutral classification.
            from app.channels.email_classifier import is_actionable_email

            _headers = {
                "Auto-Submitted": msg_data.get("auto_submitted", ""),
                "Precedence": msg_data.get("precedence", ""),
                "List-Id": msg_data.get("list_id", ""),
                "X-GitHub-Reason": msg_data.get("x_github_reason", ""),
                "X-GitHub-Sender": msg_data.get("x_github_sender", ""),
            }
            if not is_actionable_email(_headers, msg_data.get("from", "")):
                logger.info(
                    "email_skipped_notification",
                    sender=msg_data.get("from", ""),
                    subject=msg_data.get("subject", "")[:80],
                )
                continue

            if self._handler:
                inbound = InboundMessage(
                    channel_type="email",
                    channel_id=msg_data.get("from", ""),
                    sender_id=msg_data.get("from", ""),
                    sender_name=msg_data.get("from_name", ""),
                    content=msg_data.get("body", ""),
                    metadata={
                        "subject": msg_data.get("subject", ""),
                        "message_id": msg_data.get("message_id", ""),
                    },
                )
                try:
                    await self._handler(inbound)
                except Exception as e:
                    logger.error("email_handler_error", error=str(e))

    def _fetch_new_emails(self) -> list[dict[str, str]]:
        """Synchronous IMAP fetch (runs in executor)."""
        results: list[dict[str, str]] = []
        try:
            mail = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
            mail.login(self._imap_user, self._imap_password)
            mail.select("INBOX")

            _, data = mail.search(None, "UNSEEN")
            if not data or not data[0]:
                mail.logout()
                return results

            for uid in data[0].split()[:10]:  # Max 10 per poll
                _, msg_data = mail.fetch(uid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue

                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode("utf-8", errors="replace")
                                break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="replace")

                results.append({
                    "from": msg.get("From", ""),
                    "subject": msg.get("Subject", ""),
                    "body": body[:5000],
                    "message_id": msg.get("Message-ID", ""),
                    # Headers for actionable/notification classification (RFC 3834/2369).
                    "auto_submitted": msg.get("Auto-Submitted", ""),
                    "precedence": msg.get("Precedence", ""),
                    "list_id": msg.get("List-Id", ""),
                    "x_github_reason": msg.get("X-GitHub-Reason", ""),
                    "x_github_sender": msg.get("X-GitHub-Sender", ""),
                })

            mail.logout()
        except Exception as e:
            logger.error("imap_fetch_error", error=str(e))

        return results

    async def health_check(self) -> dict[str, Any]:
        return {
            "channel": "email",
            "status": "ok" if self._connected else "disconnected",
            "imap": self._imap_host,
            "smtp": self._smtp_host,
        }
