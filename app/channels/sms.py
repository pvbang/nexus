"""
MekongAI Nexus - SMS Channel Adapter (Hermes-agent inspired).

SMS messaging via Twilio API. Supports inbound webhooks + outbound.

Inspired by: hermes-agent/gateway/platforms/sms.py
Requires: pip install twilio (optional)
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlencode

import structlog

from app.channels.base import AbstractChannel, InboundMessage, OutboundMessage

logger = structlog.get_logger(__name__)


class SMSChannel(AbstractChannel):
    """SMS channel via Twilio.

    Configuration:
    - account_sid: Twilio Account SID
    - auth_token: Twilio Auth Token
    - phone_number: Twilio phone number (from number)
    - webhook_url: URL for inbound SMS webhooks
    """

    def __init__(
        self,
        account_sid: str = "",
        auth_token: str = "",
        phone_number: str = "",
        webhook_url: str = "",
    ) -> None:
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._phone_number = phone_number
        self._webhook_url = webhook_url
        self._handler: Optional[Callable[[InboundMessage], Coroutine]] = None
        self._connected = False

    @property
    def channel_type(self) -> str:
        return "sms"

    async def connect(self) -> None:
        if not self._account_sid or not self._auth_token:
            logger.warning("sms_channel_missing_credentials")
            return
        self._connected = True
        logger.info("sms_channel_connected", phone=self._phone_number)

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("sms_channel_disconnected")

    def set_message_handler(self, handler: Callable) -> None:
        self._handler = handler

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        """Send SMS via Twilio REST API."""
        if not self._connected:
            return {"error": "SMS channel not connected"}

        try:
            import httpx

            url = f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Messages.json"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    auth=(self._account_sid, self._auth_token),
                    data={
                        "To": message.channel_id,
                        "From": self._phone_number,
                        "Body": message.content[:1600],  # SMS limit
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            logger.info("sms_sent", to=message.channel_id, sid=data.get("sid"))
            return {"status": "sent", "sid": data.get("sid")}

        except Exception as e:
            logger.error("sms_send_error", error=str(e))
            return {"error": str(e)}

    def validate_twilio_signature(
        self,
        url: str,
        params: dict[str, str],
        signature: str,
    ) -> bool:
        """Validate Twilio webhook signature."""
        if not self._auth_token:
            return True

        # Build validation string: URL + sorted params
        data = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
        expected = hmac.new(
            self._auth_token.encode(),
            data.encode(),
            hashlib.sha1,
        ).digest()

        import base64
        expected_b64 = base64.b64encode(expected).decode()
        return hmac.compare_digest(expected_b64, signature)

    async def handle_webhook(
        self,
        params: dict[str, str],
        signature: str = "",
        request_url: str = "",
    ) -> Optional[str]:
        """Handle inbound SMS via Twilio webhook.

        Twilio sends POST with form data:
        - From: sender phone number
        - To: your Twilio number
        - Body: message text
        - MessageSid: unique message ID
        """
        if not self._handler:
            return None

        # Validate signature if auth_token is set
        if self._auth_token and signature and request_url:
            if not self.validate_twilio_signature(request_url, params, signature):
                logger.warning("sms_invalid_signature")
                return None

        body = params.get("Body", "").strip()
        sender = params.get("From", "")
        message_sid = params.get("MessageSid", "")

        if not body or not sender:
            return None

        inbound = InboundMessage(
            channel_type="sms",
            channel_id=sender,
            sender_id=sender,
            content=body,
            metadata={"message_sid": message_sid},
        )

        try:
            result = await self._handler(inbound)

            # Return TwiML response
            if result:
                return (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    f"<Response><Message>{result[:1600]}</Message></Response>"
                )
            return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

        except Exception as e:
            logger.error("sms_handler_error", error=str(e))
            return None

    async def health_check(self) -> dict[str, Any]:
        return {
            "channel": "sms",
            "status": "ok" if self._connected else "disconnected",
            "phone": self._phone_number,
            "provider": "twilio",
        }
