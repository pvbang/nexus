"""
MekongAI Nexus - OpenClaw Inbound Webhook Router.

FastAPI router that receives messages from OpenClaw hooks
and routes them to the Nexus orchestrator.

Flow:
    OpenClaw Channel → Hook fires → POST /api/v1/openclaw/inbound → Nexus Orchestrator
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status

from app.openclaw.config import get_openclaw_config
from app.openclaw.models import InboundMessage, InboundEvent

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/openclaw", tags=["openclaw"])

# In-memory message queue for when orchestrator is busy
_message_queue: list[InboundMessage] = []
_MAX_QUEUE_SIZE = 1000


@router.post("/inbound")
async def receive_inbound(
    request: Request,
    x_internal_token: str = Header(default="", alias="X-Internal-Token"),
    x_openclaw_signature: str = Header(default="", alias="X-OpenClaw-Signature"),
) -> dict[str, Any]:
    """Receive inbound messages from OpenClaw hooks.

    OpenClaw is configured to POST to this endpoint when messages arrive
    on any channel (Telegram, Discord, Slack, etc.).

    Security: Validates X-Internal-Token header against configured secret.

    Args:
        request: FastAPI Request with JSON body.
        x_internal_token: Internal secret for webhook verification.
        x_openclaw_signature: Optional HMAC signature.

    Returns:
        Acknowledgement with processing status.
    """
    # Verify webhook authenticity
    config = get_openclaw_config()
    if config.internal_webhook_secret:
        if not _verify_webhook(x_internal_token, x_openclaw_signature, config.internal_webhook_secret):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid webhook token",
            )

    # Parse body
    try:
        body = await request.json()
        message = InboundMessage(**body)
    except Exception as exc:
        logger.warning("openclaw_inbound_parse_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid message format: {exc}",
        )

    logger.info(
        "openclaw_inbound_received",
        event=message.event.value,
        channel=message.channel,
        sender=message.sender[:20] if message.sender else "",
        content_len=len(message.content),
    )

    # Route based on event type
    if message.event == InboundEvent.MESSAGE:
        result = await _handle_message(message)
    elif message.event == InboundEvent.COMMAND:
        result = await _handle_command(message)
    elif message.event == InboundEvent.APPROVAL_RESPONSE:
        result = await _handle_approval_response(message)
    elif message.event == InboundEvent.TYPING:
        result = {"status": "acknowledged", "action": "typing_ignored"}
    else:
        result = {"status": "acknowledged", "action": "event_logged"}

    return result


@router.get("/status")
async def openclaw_status() -> dict[str, Any]:
    """Get OpenClaw integration status."""
    from app.openclaw.bridge import get_openclaw_bridge

    bridge = get_openclaw_bridge()
    return {
        "bridge": bridge.get_info(),
        "queue_size": len(_message_queue),
        "max_queue_size": _MAX_QUEUE_SIZE,
    }


@router.post("/health")
async def openclaw_health_report(request: Request) -> dict[str, str]:
    """Receive health status updates from OpenClaw."""
    body = await request.json()
    logger.debug("openclaw_health_report", data=body)
    return {"status": "acknowledged"}


# ─────────────────────────────────────────────────────────────────
# Message Handlers
# ─────────────────────────────────────────────────────────────────


async def _handle_message(message: InboundMessage) -> dict[str, Any]:
    """Handle a regular user message.

    Routes to Nexus orchestrator for processing:
    1. Load session context
    2. Recall memories
    3. Run RAG if needed
    4. Generate response via LLM
    5. Send response back via OpenClaw bridge
    """
    try:
        # Import here to avoid circular imports
        from app.services.orchestrator import get_orchestrator

        orchestrator = get_orchestrator()

        # Process message through Nexus intelligence layer
        result = await orchestrator.process_message(
            content=message.content,
            session_id=message.session_id,
            channel=message.channel,
            sender=message.sender,
            metadata={
                "sender_name": message.sender_name,
                "reply_to": message.reply_to,
                "thread_id": message.thread_id,
                "attachments": [a.model_dump() for a in message.attachments],
                "source": "openclaw",
                **message.metadata,
            },
        )

        # Send response back via OpenClaw bridge
        if result and result.get("response"):
            from app.openclaw.bridge import get_openclaw_bridge

            bridge = get_openclaw_bridge()
            await bridge.send_chat(
                session_id=message.session_id,
                messages=[{"role": "assistant", "content": result["response"]}],
            )

        return {
            "status": "processed",
            "session_id": message.session_id,
            "response_length": len(result.get("response", "")) if result else 0,
        }

    except Exception as exc:
        logger.exception("openclaw_message_handler_error",
                         session=message.session_id, error=str(exc))
        # Queue message for retry
        if len(_message_queue) < _MAX_QUEUE_SIZE:
            _message_queue.append(message)
        return {
            "status": "queued",
            "error": str(exc),
            "queue_position": len(_message_queue),
        }


async def _handle_command(message: InboundMessage) -> dict[str, Any]:
    """Handle a slash command from OpenClaw channel."""
    command = message.content.split()[0] if message.content else ""
    args = message.content[len(command):].strip() if len(message.content) > len(command) else ""

    logger.info("openclaw_command_received",
                command=command, channel=message.channel)

    # Built-in commands
    if command in ("/status", "/health"):
        from app.openclaw.bridge import get_openclaw_bridge
        bridge = get_openclaw_bridge()
        info = bridge.get_info()
        response = f"🟢 MekongAI Nexus running\nOpenClaw: {'🟢 healthy' if info['healthy'] else '🔴 unhealthy'}"
        await bridge.send_chat(
            session_id=message.session_id,
            messages=[{"role": "assistant", "content": response}],
        )
        return {"status": "processed", "command": command}

    elif command == "/memory":
        from app.memory.active_memory import get_active_memory
        mgr = get_active_memory(message.sender or "default")
        stats = await mgr.get_stats()
        response = f"📝 Memory: {stats['total_entries']} entries, {stats['file_size_bytes']} bytes"
        from app.openclaw.bridge import get_openclaw_bridge
        bridge = get_openclaw_bridge()
        await bridge.send_chat(
            session_id=message.session_id,
            messages=[{"role": "assistant", "content": response}],
        )
        return {"status": "processed", "command": command}

    elif command == "/audit":
        from app.security.audit import get_security_audit_runner
        runner = get_security_audit_runner()
        report = await runner.run_audit(deep=False)
        response = report.format_summary()
        from app.openclaw.bridge import get_openclaw_bridge
        bridge = get_openclaw_bridge()
        await bridge.send_chat(
            session_id=message.session_id,
            messages=[{"role": "assistant", "content": response}],
        )
        return {"status": "processed", "command": command}

    else:
        # Treat as regular message
        return await _handle_message(message)


async def _handle_approval_response(message: InboundMessage) -> dict[str, Any]:
    """Handle approval/denial response from user.

    When a tool needs approval, the approval manager sends a prompt
    to the user's channel. The user responds with approve/deny.
    OpenClaw routes that response back here.
    """
    from app.core.approval import get_approval_manager

    approval_mgr = get_approval_manager()
    content = message.content.strip().lower()
    request_id = message.metadata.get("approval_request_id", "")

    if not request_id:
        # Try to find pending request for this session
        pending = approval_mgr.get_pending()
        session_pending = [p for p in pending if p.get("session_id") == message.session_id]
        if session_pending:
            request_id = session_pending[0]["id"]

    if not request_id:
        return {"status": "no_pending_approval"}

    # Determine decision
    approve_signals = {"yes", "y", "approve", "✅", "ok", "allow", "đồng ý"}
    deny_signals = {"no", "n", "deny", "❌", "block", "reject", "từ chối"}

    if content in approve_signals:
        await approval_mgr.approve(request_id, decided_by=message.sender)
        return {"status": "approved", "request_id": request_id}
    elif content in deny_signals:
        await approval_mgr.deny(request_id, decided_by=message.sender, reason="User denied")
        return {"status": "denied", "request_id": request_id}
    else:
        return {"status": "unrecognized_response", "content": content}


# ─────────────────────────────────────────────────────────────────
# Webhook Verification
# ─────────────────────────────────────────────────────────────────


def _verify_webhook(token: str, signature: str, secret: str) -> bool:
    """Verify webhook authenticity.

    Checks either:
    1. Direct token match (simple)
    2. HMAC signature verification (secure)
    """
    # Method 1: Direct token comparison
    if token and hmac.compare_digest(token, secret):
        return True

    # Method 2: HMAC signature (if provided)
    if signature:
        expected = hmac.new(
            secret.encode(), token.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    return False
