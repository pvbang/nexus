"""Per-worker subscriber for the cross-process cancel channel.

Each Uvicorn worker subscribes to CANCEL_CHANNEL. When a cancel is published,
every worker runs handle_cancel_message; the one that OWNS the task cancels it
(real asyncio cancel via TaskRunner). Non-owning workers no-op.

The MessageBus delivers the raw envelope's `payload` to handlers via the
'nexus:events'-style dispatch, but for this dedicated channel we subscribe
directly, so the handler receives the full envelope and reads its 'payload'.
"""

from __future__ import annotations

import structlog

from app.core.cancel_signal import CANCEL_CHANNEL, parse_cancel_payload
from app.core.task_runner import get_task_runner

logger = structlog.get_logger(__name__)


async def handle_cancel_message(message: dict) -> bool:
    """Handle one cancel message. Returns True if THIS worker cancelled the task.

    `message` may be either the raw payload ({"task_id","session_id"}) or a
    MessageBus envelope wrapping it under "payload"; we accept both.
    """
    payload = message.get("payload", message) if isinstance(message, dict) else {}
    task_id, session_id = parse_cancel_payload(payload or {})
    if not task_id:
        return False

    # Best-effort cooperative token (harmless if this worker lacks it).
    try:
        from app.core.cancellation import cancel_task as cancel_token_fn
        cancel_token_fn(task_id)
    except Exception:
        pass

    if not session_id:
        return False

    try:
        runner = get_task_runner()
        cancelled = await runner.cancel_task(session_id)
        if cancelled:
            logger.info("cancel_signal_applied", task_id=task_id, session_id=session_id)
        return bool(cancelled)
    except Exception as exc:
        logger.warning("cancel_signal_apply_failed", task_id=task_id, error=str(exc))
        return False


async def register_cancel_subscriber(bus) -> None:
    """Subscribe this worker to the cancel channel."""
    await bus.subscribe(CANCEL_CHANNEL, handle_cancel_message)
    logger.info("cancel_subscriber_registered", channel=CANCEL_CHANNEL)
