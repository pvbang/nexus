"""
Nexus - Webhook Integration (OpenClaw-inspired).

Receive external event triggers and route them to agents/sessions.

Inspired by: OpenClaw webhooks + Gmail Pub/Sub automation
"""

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class WebhookEvent:
    """An inbound webhook event."""
    event_id: str
    source: str             # "github", "gmail", "custom", etc.
    event_type: str         # "push", "email.received", etc.
    payload: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    received_at: float = field(default_factory=time.time)
    session_id: str = "main"


@dataclass
class WebhookRegistration:
    """A registered webhook endpoint."""
    webhook_id: str
    name: str
    secret: str = ""
    target_session: str = "main"
    event_filter: str = ""  # regex filter on event_type
    active: bool = True


WebhookHandler = Callable[[WebhookEvent], Awaitable[Optional[str]]]


class WebhookManager:
    """
    Manage webhook registrations and event processing.

    OpenClaw pattern: webhooks deliver events into agent sessions.
    """

    def __init__(self) -> None:
        self._registrations: dict[str, WebhookRegistration] = {}
        self._handler: Optional[WebhookHandler] = None

    def set_handler(self, handler: WebhookHandler) -> None:
        self._handler = handler

    def register(self, name: str, secret: str = "",
                 target_session: str = "main") -> WebhookRegistration:
        """Register a new webhook. Returns the registration with its id."""
        reg = WebhookRegistration(
            webhook_id=uuid.uuid4().hex[:12],
            name=name,
            secret=secret,
            target_session=target_session,
        )
        self._registrations[reg.webhook_id] = reg
        logger.info("webhook_registered", id=reg.webhook_id, name=name)
        return reg

    def unregister(self, webhook_id: str) -> bool:
        return self._registrations.pop(webhook_id, None) is not None

    def verify_signature(self, webhook_id: str, body: bytes,
                         signature: str) -> bool:
        """Verify HMAC signature for a webhook."""
        reg = self._registrations.get(webhook_id)
        if not reg or not reg.secret:
            return True  # No secret = no verification
        expected = hmac.new(
            reg.secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def process_event(self, webhook_id: str,
                            event_type: str,
                            payload: dict[str, Any],
                            headers: Optional[dict[str, str]] = None) -> Optional[str]:
        """Process an inbound webhook event."""
        reg = self._registrations.get(webhook_id)
        if not reg or not reg.active:
            logger.warning("webhook_not_found", id=webhook_id)
            return None

        event = WebhookEvent(
            event_id=uuid.uuid4().hex[:12],
            source=reg.name,
            event_type=event_type,
            payload=payload,
            headers=headers or {},
            session_id=reg.target_session,
        )

        logger.info("webhook_event", id=event.event_id,
                     source=event.source, type=event_type)

        if self._handler:
            return await self._handler(event)
        return None

    def list_registrations(self) -> list[dict[str, Any]]:
        return [
            {"webhook_id": r.webhook_id, "name": r.name,
             "target_session": r.target_session, "active": r.active}
            for r in self._registrations.values()
        ]


_manager: Optional[WebhookManager] = None


def get_webhook_manager() -> WebhookManager:
    global _manager
    if _manager is None:
        _manager = WebhookManager()
    return _manager
