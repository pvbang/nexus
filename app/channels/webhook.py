"""
Nexus - Webhook Channel Adapter (Hermes-agent inspired).

Generic HTTP webhook receiver for external event triggers.
Supports signature validation and configurable event routing.

Inspired by: hermes-agent/gateway/platforms/webhook.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any, Callable, Coroutine, Optional

import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage

logger = structlog.get_logger(__name__)


class WebhookChannel(AbstractChannel):
    """Generic webhook channel — receives HTTP POST callbacks.

    Configuration via environment:
    - WEBHOOK_SECRET: shared secret for signature validation
    - WEBHOOK_PATH: URL path to listen on (default: /webhook)

    The channel doesn't maintain a persistent connection. Instead,
    it exposes a method ``handle_webhook()`` that API endpoints call
    when a webhook POST is received.
    """

    def __init__(
        self,
        secret: str = "",
        path: str = "/webhook",
    ) -> None:
        self._secret = secret
        self._path = path
        self._handler: Optional[Callable[[InboundMessage], Coroutine]] = None
        self._connected = False

    @property
    def channel_type(self) -> str:
        return "webhook"

    async def connect(self) -> None:
        self._connected = True
        logger.info("webhook_channel_connected", path=self._path)

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("webhook_channel_disconnected")

    def set_message_handler(self, handler: Callable) -> None:
        self._handler = handler

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        """Webhooks are receive-only by default. Override for outbound webhooks."""
        logger.debug("webhook_send_noop", content_len=len(message.content))
        return {"status": "webhook_channel_is_receive_only"}

    def validate_signature(
        self,
        payload: bytes,
        signature: str,
        algorithm: str = "sha256",
    ) -> bool:
        """Validate webhook signature using HMAC.

        Supports common webhook providers:
        - GitHub: ``X-Hub-Signature-256: sha256=<hex>``
        - Stripe: ``Stripe-Signature: t=<ts>,v1=<hex>``
        - Generic: ``X-Signature: <hex>``
        """
        if not self._secret:
            return True  # No secret configured, accept all

        expected = hmac.new(
            self._secret.encode(),
            payload,
            getattr(hashlib, algorithm, hashlib.sha256),
        ).hexdigest()

        # Strip algorithm prefix if present
        actual = signature
        if "=" in actual:
            actual = actual.split("=", 1)[-1]

        return hmac.compare_digest(expected, actual)

    async def handle_webhook(
        self,
        payload: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
        raw_body: Optional[bytes] = None,
    ) -> Optional[str]:
        """Process an incoming webhook POST.

        Called by the API endpoint when a webhook is received.

        Parameters
        ----------
        payload:
            Parsed JSON body.
        headers:
            HTTP headers (for signature validation).
        raw_body:
            Raw request body bytes (for signature validation).

        Returns
        -------
        Optional[str]:
            Response message from the handler, or None.
        """
        if not self._connected or self._handler is None:
            return None

        # Validate signature if secret is configured
        if self._secret and headers and raw_body:
            sig = (
                headers.get("x-hub-signature-256", "")
                or headers.get("x-signature", "")
                or headers.get("x-webhook-signature", "")
            )
            if sig and not self.validate_signature(raw_body, sig):
                logger.warning("webhook_signature_invalid")
                return None

        # Extract message from payload
        content = (
            payload.get("message", "")
            or payload.get("text", "")
            or payload.get("content", "")
            or payload.get("body", "")
            or json.dumps(payload, ensure_ascii=False)[:2000]
        )

        sender_id = str(
            payload.get("sender_id", "")
            or payload.get("user_id", "")
            or payload.get("from", "")
            or "webhook_user"
        )

        channel_id = str(
            payload.get("channel_id", "")
            or payload.get("room", "")
            or payload.get("source", "webhook")
        )

        inbound = InboundMessage(
            channel_type="webhook",
            channel_id=channel_id,
            sender_id=sender_id,
            content=content,
            metadata={"headers": dict(headers) if headers else {}, "raw_payload": payload},
        )

        try:
            result = await self._handler(inbound)
            return result
        except Exception as e:
            logger.error("webhook_handler_error", error=str(e))
            return None

    async def health_check(self) -> dict[str, Any]:
        return {
            "channel": "webhook",
            "status": "ok" if self._connected else "disconnected",
            "path": self._path,
        }
