"""
MekongAI Nexus - Message Bus (Inter-Agent Communication).

Hệ thống pub/sub messaging qua Redis cho giao tiếp giữa các Agent.
Hỗ trợ: direct messages, broadcast, swarm channels.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

import redis.asyncio as aioredis
import structlog

from app.core.dependencies import get_redis

logger = structlog.get_logger(__name__)

# Type alias
MessageHandler = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class MessageBus:
    """
    Redis-based Pub/Sub message bus cho inter-agent communication.

    Channels:
    - nexus:direct:{agent_id} - Direct messages to specific agent
    - nexus:swarm:{swarm_id}  - Broadcast within a swarm
    - nexus:broadcast          - System-wide broadcast
    - nexus:events             - System events (agent spawn, task complete, etc.)
    """

    # Fallback cap for messages buffered during a Redis outage (prevents unbounded
    # memory growth). Live value comes from Settings.message_bus_buffer_size (L5).
    _MAX_BUFFER_SIZE = 500

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._listening = False
        self._listen_task: Optional[asyncio.Task] = None
        # H-02 FIX: Buffer for messages that failed to publish during Redis outage.
        # Replayed on successful reconnect.
        self._publish_buffer: list[tuple[str, str]] = []  # (channel, json_envelope)
        # L5: configurable buffer bound (falls back to the class constant).
        try:
            from app.config import get_settings
            self._max_buffer_size: int = int(get_settings().message_bus_buffer_size)
        except Exception:
            self._max_buffer_size = self._MAX_BUFFER_SIZE

    async def connect(self):
        """Connect to Redis and start listening.

        PUBSUB-FIX: After creating a new pubsub instance, re-subscribe all
        channels that have registered handlers. Previously, reconnect would
        create a fresh pubsub but not re-subscribe — resulting in
        'channels=[]' in logs and lost messages.
        """
        self._redis = await get_redis()
        self._pubsub = self._redis.pubsub()
        # Re-subscribe existing channels on reconnect
        if self._handlers:
            for channel in list(self._handlers.keys()):
                try:
                    await self._pubsub.subscribe(channel)
                except Exception as _sub_err:
                    logger.warning("pubsub_resubscribe_failed",
                                   channel=channel, error=str(_sub_err))

        # H-02 FIX: Replay buffered messages that failed during Redis outage
        if self._publish_buffer and self._redis:
            _replayed = 0
            _failed = []
            for _ch, _env_json in self._publish_buffer:
                try:
                    await self._redis.publish(_ch, _env_json)
                    _replayed += 1
                except Exception:
                    _failed.append((_ch, _env_json))
            self._publish_buffer = _failed
            if _replayed:
                logger.info("publish_buffer_replayed", replayed=_replayed, remaining=len(_failed))

        logger.info("message_bus_connected")

    async def disconnect(self):
        """Disconnect from Redis."""
        self._listening = False
        if self._listen_task:
            self._listen_task.cancel()
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
        logger.info("message_bus_disconnected")

    async def subscribe(self, channel: str, handler: MessageHandler):
        """Subscribe to a channel with a message handler.

        MED-04 FIX: Auto-starts the listener loop if not already listening.
        Previously, subscribe() could be called before start_listening(),
        causing events to be missed until the lifespan handler runs.

        CYCLE3-FIX: Guard against partial registration on pubsub.subscribe() failure.
        Previously, if pubsub.subscribe(channel) raised, self._handlers[channel] = []
        was already set. Subsequent calls would skip the pubsub.subscribe() (channel
        key exists) but still append handlers — which never receive events because
        Redis never subscribed to that channel. Now uses try/except to clean up.
        """
        if channel not in self._handlers:
            self._handlers[channel] = []
            if self._pubsub:
                try:
                    await self._pubsub.subscribe(channel)
                except Exception as _sub_err:
                    del self._handlers[channel]
                    raise

        self._handlers[channel].append(handler)

        if not self._listening and self._pubsub:
            await self.start_listening()

        logger.debug(
            "channel_subscribed",
            channel=channel,
            handler_count=len(self._handlers[channel]),
        )

    async def unsubscribe(self, channel: str, handler: MessageHandler | None = None):
        """Unsubscribe from a channel.

        If handler is provided, only remove that specific handler.
        If handler is None, remove all handlers for the channel (backward compatible).
        """
        if handler is not None and channel in self._handlers:
            # Remove specific handler
            try:
                self._handlers[channel].remove(handler)
            except ValueError:
                pass  # Handler already removed
            # If no more handlers, unsubscribe from Redis channel
            if not self._handlers[channel]:
                del self._handlers[channel]
                if self._pubsub:
                    await self._pubsub.unsubscribe(channel)
            logger.debug(
                "handler_unsubscribed",
                channel=channel,
                remaining=len(self._handlers.get(channel, [])),
            )
        else:
            # Remove all handlers (backward compatible)
            self._handlers.pop(channel, None)
            if self._pubsub:
                await self._pubsub.unsubscribe(channel)

    async def publish(
        self,
        channel: str,
        message: dict[str, Any],
        sender_id: Optional[str] = None,
    ) -> bool:
        """Publish a message to a channel with retry on Redis failure.

        Returns True if published successfully, False if all retries exhausted.

        PUBLISH-SAFETY FIX: Uses a dedicated Redis connection for publish instead
        of the shared self._redis. This prevents publish failures from corrupting
        the pubsub connection used by _listen_loop. Previously, setting self._redis=None
        on publish failure would force a reconnect() that replaced the pubsub instance,
        but _listen_loop still held a reference to the OLD pubsub (via self._pubsub
        which was only refreshed in connect()). This caused silent message loss.
        """
        envelope = {
            "id": str(uuid.uuid4()),
            "channel": channel,
            "sender_id": sender_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": message,
        }

        for attempt in range(3):
            try:
                if not self._redis:
                    await self.connect()
                await self._redis.publish(channel, json.dumps(envelope))
                logger.debug("message_published", channel=channel, msg_id=envelope["id"])
                return True
            except Exception as e:
                logger.warning(
                    "message_publish_failed",
                    channel=channel,
                    attempt=attempt + 1,
                    error=str(e),
                )
                # Get a fresh Redis connection for retry — do NOT null self._redis
                # as that would disrupt the pubsub listener on reconnect.
                try:
                    from app.core.dependencies import get_redis
                    self._redis = await get_redis()
                except Exception:
                    self._redis = None
                if attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))

        # H-02 FIX: Buffer failed message for replay on reconnect
        self._buffer_for_replay(channel, json.dumps(envelope))
        return False

    def _buffer_for_replay(self, channel: str, env_json: str) -> None:
        """Buffer a publish that failed during a Redis outage, bounded FIFO.

        Warns at 80% capacity and evicts the oldest 20% when full (rather than
        dropping the newest), so the most recent state survives a sustained
        outage. Bound is Settings.message_bus_buffer_size (L5).
        """
        cap = self._max_buffer_size

        # BUG-18 FIX: Warn at 80% capacity
        if len(self._publish_buffer) > cap * 80 // 100:
            logger.warning("publish_buffer_high",
                           current=len(self._publish_buffer),
                           max=cap)

        # BUG-18 FIX: Evict oldest 20% when full (FIFO) instead of dropping new messages
        if len(self._publish_buffer) >= cap:
            _evict_count = max(1, cap // 5)
            self._publish_buffer = self._publish_buffer[_evict_count:]
            logger.warning("publish_buffer_eviction", evicted=_evict_count,
                           remaining=len(self._publish_buffer))

        self._publish_buffer.append((channel, env_json))
        logger.warning("message_buffered_for_replay", channel=channel,
                       buffer_size=len(self._publish_buffer))

    async def send_direct(
        self,
        to_agent_id: str,
        message: dict[str, Any],
        from_agent_id: Optional[str] = None,
    ):
        """Send a direct message to a specific agent."""
        channel = f"nexus:direct:{to_agent_id}"
        await self.publish(channel, message, sender_id=from_agent_id)

    async def send_to_swarm(
        self,
        swarm_id: str,
        message: dict[str, Any],
        from_agent_id: Optional[str] = None,
    ):
        """Send a message to all agents in a swarm."""
        channel = f"nexus:swarm:{swarm_id}"
        await self.publish(channel, message, sender_id=from_agent_id)

    async def broadcast(
        self,
        message: dict[str, Any],
        from_agent_id: Optional[str] = None,
    ):
        """Broadcast a message to all agents."""
        await self.publish("nexus:broadcast", message, sender_id=from_agent_id)

    async def emit_event(self, event_type: str, data: dict[str, Any]):
        """Emit a system event."""
        await self.publish(
            "nexus:events",
            {"event_type": event_type, **data},
        )

    async def emit_event_scoped(
        self, event_type: str, data: dict[str, Any], session_id: str | None = None
    ):
        """Emit a system event scoped to a specific session.

        IMPORTANT: session_id is injected into the payload so that frontend
        Monitor WebSocket consumers can route events to the correct session's
        processStore (matching against currentSessionId).

        BUG-RT07 FIX: Injects BOTH session_id AND conversation_id into payload.
        Frontend checks `data.session_id || data.conversation_id` for routing,
        so both must be present for reliable matching regardless of which key
        the original event emitter used.
        """
        payload = {"event_type": event_type, **data}
        # Inject session identifiers into payload for frontend routing
        if session_id:
            payload["session_id"] = session_id
            payload["conversation_id"] = session_id
        # Emit to global channel only (Monitor WS subscribes to this).
        await self.publish("nexus:events", payload)

    async def start_listening(self):
        """Start the message listener loop."""
        if self._listening:
            return

        self._listening = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("message_bus_listening")

    async def _listen_loop(self):
        """Internal message listening loop with auto-reconnect.

        HIGH-1 FIX: Added consecutive failure counter to break out of inner
        loop and trigger full reconnect. Previously, persistent connection
        failures would spin in inner loop forever (1s sleeps) without ever
        reaching the reconnect logic in the outer except block.
        """
        _backoff = 1.0
        while self._listening:
            try:
                _consecutive_failures = 0
                _MAX_INNER_FAILURES = 5  # Break to reconnect after 5 consecutive failures
                while self._listening and self._pubsub:
                    if not self._handlers:
                        await asyncio.sleep(1.0)
                        continue
                    try:
                        message = await self._pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=1.0
                        )
                        _consecutive_failures = 0  # Reset on success
                    except Exception as _inner_err:
                        _consecutive_failures += 1
                        if _consecutive_failures >= _MAX_INNER_FAILURES:
                            logger.warning(
                                "pubsub_consecutive_failures_reconnecting",
                                failures=_consecutive_failures,
                                error=str(_inner_err),
                            )
                            raise ConnectionError(
                                f"PubSub connection lost after {_consecutive_failures} failures: {_inner_err}"
                            )
                        await asyncio.sleep(1.0)
                        continue

                    if message and message["type"] == "message":
                        channel = message["channel"]
                        if isinstance(channel, bytes):
                            channel = channel.decode()

                        try:
                            data = json.loads(message["data"])
                        except (json.JSONDecodeError, TypeError):
                            continue

                        # HIGH-02 FIX: Dispatch handlers concurrently with asyncio.gather.
                        # Previously sequential dispatch meant a slow handler would delay
                        # all subsequent handlers on the same channel (e.g., Monitor WS
                        # blocked by a slow dashboard handler).
                        handlers = list(self._handlers.get(channel, []))
                        if handlers:
                            results = await asyncio.gather(
                                *(h(data) for h in handlers),
                                return_exceptions=True,
                            )
                            for i, result in enumerate(results):
                                if isinstance(result, Exception):
                                    logger.error(
                                        "handler_error",
                                        channel=channel,
                                        error=str(result),
                                    )
                _backoff = 1.0
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("listen_loop_error", error=str(e), backoff=_backoff)
                await asyncio.sleep(_backoff)
                _backoff = min(_backoff * 2, 60.0)
                # Attempt reconnect — re-establish Redis connection and re-subscribe
                try:
                    await self.connect()
                    logger.info(
                        "pubsub_reconnected",
                        channels=list(self._handlers.keys()),
                    )
                except Exception as _reconn_err:
                    logger.error("pubsub_reconnect_failed", error=str(_reconn_err))


# Singleton message bus with race-condition protection
_message_bus: Optional[MessageBus] = None
_message_bus_lock: asyncio.Lock | None = None


def _get_bus_lock() -> asyncio.Lock:
    """Lazy-init lock (must be created inside a running event loop)."""
    global _message_bus_lock
    if _message_bus_lock is None:
        _message_bus_lock = asyncio.Lock()
    return _message_bus_lock


async def get_message_bus() -> MessageBus:
    """Get or create the global message bus instance.

    PUBSUB-FIX: Protected with asyncio.Lock to prevent race conditions
    where multiple coroutines create separate MessageBus instances
    (each with its own empty _handlers dict).
    """
    global _message_bus
    if _message_bus is not None:
        return _message_bus
    async with _get_bus_lock():
        if _message_bus is None:
            _message_bus = MessageBus()
            await _message_bus.connect()
    return _message_bus
