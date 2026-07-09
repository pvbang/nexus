"""
MekongAI Nexus - WebSocket Handlers.

Real-time streaming cho chat, agent monitoring, và swarm observation.
Supports both non-streaming (type: "chat") and streaming (type: "chat_stream") modes.

Architecture (post-Session Persistence fix + Hermes-agent patterns):
- WebSocket is just a "window" into a running task
- Tasks run in background via TaskRunner (survive WS disconnect)
- WS handlers subscribe to events; on disconnect, task continues
- On reconnect, client catches up via Event Replay API
- Multi-tab support: multiple WS per user, isolated per conversation
- Session-aware: integrates with SessionManager for interrupt/steer

Hermes-agent inspired enhancements (Phase 1-4):
- Per-conversation connection tracking (multi-tab isolation)
- Session-aware streaming (tokens, tool progress, interrupts)
- Reconnection support (replay missed events from task_events table)
- Interrupt/steer commands via WebSocket
- Background task notifications across all user's tabs (Phase 3)
- Auto title generation on first message (Phase 2)
- User memory extraction trigger (Phase 4)
"""

import asyncio
import collections
from datetime import datetime, timezone
import json
import time
from typing import Any, Optional

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.communication.message_bus import get_message_bus
from app.core.auth import verify_token
from app.core.session_mutex import get_session_mutex
from app.core.task_runner import get_task_runner
from app.services.orchestrator import CoreOrchestrator

logger = structlog.get_logger(__name__)
ws_router = APIRouter()


class WSRateLimiter:
    """Per-connection message rate limiter for WebSocket.

    MED-05 FIX: Uses deque with maxlen for O(1) append instead of
    list-filter O(n) on every check.
    """

    def __init__(self, max_messages: int = 10, window_seconds: float = 60.0):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self._timestamps: collections.deque = collections.deque(maxlen=max_messages + 10)

    def check(self) -> bool:
        """Return True if message is allowed, False if rate limited."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        # Remove expired from front (deque is ordered by time)
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self.max_messages:
            return False
        self._timestamps.append(now)
        return True


class ConnectionManager:
    """
    Quản lý WebSocket connections.

    Hermes-agent enhancements:
    - Per-conversation connection tracking (multi-tab support)
    - Connection metadata (user_id, tab_id, connected_at)
    - Isolated message delivery per conversation
    - PERF-4: periodic dead connection cleanup
    """

    def __init__(self):
        # channel → list of (WebSocket, metadata)
        self.active_connections: dict[str, list[tuple[WebSocket, dict[str, Any]]]] = {}
        # disconnect is not called cleanly (crash, timeout).
        # conversation_id → set of connection_ids (UUID strings)
        self._conversation_connections: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()
        self._next_conn_id = 0  # monotonically increasing, never reused

    async def connect(
        self,
        websocket: WebSocket,
        channel: str = "default",
        *,
        user_id: str = "",
        tab_id: str = "",
        conversation_id: str = "",
    ) -> None:
        """Accept and register a WebSocket connection with metadata."""
        await websocket.accept()

        async with self._lock:
            # concurrent WebSocket connects (multi-worker or burst connections).
            self._next_conn_id += 1
            conn_id = f"conn-{self._next_conn_id}"

            metadata = {
                "user_id": user_id,
                "tab_id": tab_id,
                "conversation_id": conversation_id,
                "connected_at": time.time(),
                "last_activity": time.time(),
                "conn_id": conn_id,  # Stable unique identifier
            }

            if channel not in self.active_connections:
                self.active_connections[channel] = []
            self.active_connections[channel].append((websocket, metadata))

            if conversation_id:
                if conversation_id not in self._conversation_connections:
                    self._conversation_connections[conversation_id] = set()
                self._conversation_connections[conversation_id].add(conn_id)

        logger.info(
            "ws_connected",
            channel=channel,
            user_id=user_id,
            tab_id=tab_id,
            conversation_id=conversation_id,
            total=self.total_connections,
            tabs_for_conversation=len(
                self._conversation_connections.get(conversation_id, set())
            ),
        )

    async def disconnect(self, websocket: WebSocket, channel: str = "default") -> None:
        """Remove a WebSocket connection.

        CRIT-03 FIX: Uses conn_id from metadata (stable, never reused) instead
        of id(websocket) which can be recycled after GC.
        """
        async with self._lock:
            if channel in self.active_connections:
                # Find and remove the connection + metadata
                remaining: list[tuple[WebSocket, dict[str, Any]]] = []
                removed_conv_id = ""
                removed_conn_id = ""
                for ws, meta in self.active_connections[channel]:
                    if ws != websocket:
                        remaining.append((ws, meta))
                    else:
                        removed_conv_id = meta.get("conversation_id", "")
                        removed_conn_id = meta.get("conn_id", "")
                self.active_connections[channel] = remaining

                # Remove empty channels
                if not self.active_connections[channel]:
                    del self.active_connections[channel]

                # Clean up conversation tracking using stable conn_id
                if removed_conv_id and removed_conv_id in self._conversation_connections:
                    if removed_conn_id:
                        self._conversation_connections[removed_conv_id].discard(removed_conn_id)
                    if not self._conversation_connections[removed_conv_id]:
                        del self._conversation_connections[removed_conv_id]

    async def send_to_channel(self, channel: str, message: dict[str, Any]) -> None:
        """Send message to all connections on a channel.

        BUG-THREAD-SAFETY FIX: Copy connection list before iterating to prevent
        concurrent modification from disconnect() or other coroutines.
        BUG-H1 FIX: Uses stable conn_id instead of id(ws) for dead connection matching.
        """
        conns = self.active_connections.get(channel)
        if not conns:
            return

        snapshot = list(conns)
        dead_conn_ids: list[str] = []
        for ws, meta in snapshot:
            try:
                await ws.send_json(message)
                meta["last_activity"] = time.time()
            except Exception:
                # which can be recycled by Python GC after disconnect.
                dead_conn_ids.append(meta.get("conn_id", ""))

        # Clean up dead connections from BOTH active_connections AND _conversation_connections
        if dead_conn_ids:
            dead_set = set(dead_conn_ids)
            dead_set.discard("")  # Ignore entries without conn_id
            async with self._lock:
                remaining: list[tuple[WebSocket, dict[str, Any]]] = []
                for ws, meta in self.active_connections.get(channel, []):
                    if meta.get("conn_id", "") in dead_set:
                        # Clean _conversation_connections tracking
                        conv_id = meta.get("conversation_id", "")
                        conn_id = meta.get("conn_id", "")
                        if conv_id and conv_id in self._conversation_connections:
                            if conn_id:
                                self._conversation_connections[conv_id].discard(conn_id)
                            if not self._conversation_connections[conv_id]:
                                del self._conversation_connections[conv_id]
                    else:
                        remaining.append((ws, meta))
                self.active_connections[channel] = remaining
                if not remaining:
                    del self.active_connections[channel]

    async def send_to_conversation(
        self,
        conversation_id: str,
        message: dict[str, Any],
        *,
        exclude_ws: Optional[WebSocket] = None,
    ) -> int:
        """
        Send message to ALL WebSocket connections for a conversation.

        Multi-tab support: delivers to all tabs viewing the same conversation.
        Returns count of connections message was sent to.

        BUG-H1 FIX: Uses stable conn_id instead of id(ws) for dead connection matching.
        """
        channel = f"chat:{conversation_id}"
        conns = self.active_connections.get(channel)
        if not conns:
            return 0

        # Snapshot to prevent concurrent modification
        snapshot = list(conns)
        sent = 0
        dead_conn_ids: list[str] = []
        for ws, meta in snapshot:
            if ws == exclude_ws:
                continue
            try:
                await ws.send_json(message)
                meta["last_activity"] = time.time()
                sent += 1
            except Exception:
                dead_conn_ids.append(meta.get("conn_id", ""))

        # Clean up dead connections from BOTH active_connections AND _conversation_connections
        if dead_conn_ids:
            dead_set = set(dead_conn_ids)
            dead_set.discard("")
            async with self._lock:
                remaining: list[tuple[WebSocket, dict[str, Any]]] = []
                for ws, meta in self.active_connections.get(channel, []):
                    if meta.get("conn_id", "") in dead_set:
                        conv_id = meta.get("conversation_id", "")
                        conn_id = meta.get("conn_id", "")
                        if conv_id and conv_id in self._conversation_connections:
                            if conn_id:
                                self._conversation_connections[conv_id].discard(conn_id)
                            if not self._conversation_connections[conv_id]:
                                del self._conversation_connections[conv_id]
                    else:
                        remaining.append((ws, meta))
                self.active_connections[channel] = remaining
                if not remaining:
                    del self.active_connections[channel]

        return sent

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Broadcast to all connections across all channels."""
        for channel in list(self.active_connections.keys()):
            await self.send_to_channel(channel, message)

    @property
    def total_connections(self) -> int:
        """Total active connections across all channels."""
        return sum(len(conns) for conns in self.active_connections.values())

    def get_conversation_tab_count(self, conversation_id: str) -> int:
        """Get number of active tabs/connections for a conversation."""
        return len(self._conversation_connections.get(conversation_id, set()))

    def get_connection_stats(self) -> dict[str, Any]:
        """Get connection statistics for monitoring."""
        return {
            "total_connections": self.total_connections,
            "channels": len(self.active_connections),
            "conversations_tracked": len(self._conversation_connections),
            "per_conversation": {
                conv_id: len(ws_ids)
                for conv_id, ws_ids in self._conversation_connections.items()
            },
        }

    async def cleanup_dead_connections(self) -> int:
        """PERF-4: Probe and remove dead connections. Returns count removed.

        CRIT-01 FIX: Snapshot connection list before iteration to prevent
        RuntimeError from concurrent modification. Between awaits (ws.send_json),
        other coroutines can call connect()/disconnect() which mutates the list.
        """
        removed = 0
        for channel in list(self.active_connections.keys()):
            dead: list[WebSocket] = []
            # CRIT-01: Snapshot to prevent "list changed during iteration"
            conns_snapshot = list(self.active_connections.get(channel, []))
            for ws, meta in conns_snapshot:
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    dead.append(ws)
            for ws in dead:
                await self.disconnect(ws, channel)
                removed += 1
        if removed:
            logger.info("ws_cleanup_dead", removed=removed, remaining=self.total_connections)
        return removed


manager = ConnectionManager()


async def _persist_user_message_ws(
    session_id: str,
    content: str,
) -> None:
    """Persist user message to DB for WS paths (best-effort).

    Only persists USER messages. Assistant messages are persisted by
    TaskRunner._persist_assistant_message (single persist path).
    """
    try:
        from app.core.dependencies import _session_factory as _sf
        from app.models.database import ConversationMessageModel

        if not _sf:
            return
        async with _sf() as _db:
            from sqlalchemy import select as _sel, func as _fn
            from datetime import datetime, timedelta, timezone
            _cutoff = datetime.now(timezone.utc) - timedelta(seconds=30)
            _dup_result = await _db.execute(
                _sel(_fn.count()).select_from(ConversationMessageModel).where(
                    ConversationMessageModel.session_id == session_id,
                    ConversationMessageModel.role == "user",
                    ConversationMessageModel.content == content,
                    ConversationMessageModel.created_at >= _cutoff,
                )
            )
            _dup_count = _dup_result.scalar()
            if _dup_count and _dup_count > 0:
                return

            _db.add(ConversationMessageModel(
                session_id=session_id,
                role="user",
                content=content,
            ))
            await _db.commit()
    except Exception as exc:
        logger.warning("ws_user_message_persist_failed", error=str(exc), session_id=session_id)


async def _replay_missed_events(
    websocket: WebSocket,
    conversation_id: str,
    since_sequence: int = 0,
) -> int:
    """
    Replay missed events from task_events table for reconnection support.

    Queries events since the given sequence number and sends them to the
    reconnecting client. Returns count of events replayed.

    BUG-REPLAY-SEQ FIX: Sequences are PER-TASK (restart at 0 for each new task).
    Using `sequence > since_sequence` across ALL tasks causes events from new
    tasks (with low sequence numbers) to be filtered out when since_sequence
    is high from a previous task.

    FIX: Use created_at-based ordering as the PRIMARY filter instead of sequence.
    Convert since_sequence to a timestamp by looking up the event with that sequence
    in the most recent task. Then query all events AFTER that timestamp.
    If since_sequence is 0, replay from the LATEST task only (avoid flooding).

    Hermes-agent pattern: clients track last_sequence and request replay
    on reconnect to get caught up without data loss.
    """
    try:
        from app.core.dependencies import _session_factory as _sf
        from app.models.database import TaskEventModel
        from sqlalchemy import select

        if not _sf:
            return 0

        async with _sf() as db:
            if since_sequence > 0:
                # received. Then replay ALL events after that timestamp (regardless of
                # which task they belong to). This handles the cross-task sequence restart.
                last_seen_result = await db.execute(
                    select(TaskEventModel.created_at)
                    .where(
                        TaskEventModel.session_id == conversation_id,
                        TaskEventModel.sequence == since_sequence,
                    )
                    .order_by(TaskEventModel.created_at.desc())
                    .limit(1)
                )
                last_seen_at = last_seen_result.scalar()

                if last_seen_at:
                    # Replay events AFTER the last seen timestamp
                    events_result = await db.execute(
                        select(TaskEventModel)
                        .where(
                            TaskEventModel.session_id == conversation_id,
                            TaskEventModel.created_at > last_seen_at,
                        )
                        .order_by(
                            TaskEventModel.created_at.asc(),
                            TaskEventModel.sequence.asc().nullslast(),
                            TaskEventModel.id.asc(),
                        )
                        .limit(200)
                    )
                else:
                    # Get latest task's events in ASC order (consistent, no reversal needed).
                    latest_task_result = await db.execute(
                        select(TaskEventModel.task_id)
                        .where(TaskEventModel.session_id == conversation_id)
                        .order_by(TaskEventModel.created_at.desc())
                        .limit(1)
                    )
                    _fallback_task_id = latest_task_result.scalar()
                    if _fallback_task_id:
                        events_result = await db.execute(
                            select(TaskEventModel)
                            .where(
                                TaskEventModel.session_id == conversation_id,
                                TaskEventModel.task_id == _fallback_task_id,
                            )
                            .order_by(
                                TaskEventModel.created_at.asc(),
                                TaskEventModel.sequence.asc().nullslast(),
                                TaskEventModel.id.asc(),
                            )
                            .limit(200)
                        )
                    else:
                        events_result = await db.execute(
                            select(TaskEventModel)
                            .where(TaskEventModel.session_id == conversation_id)
                            .order_by(
                                TaskEventModel.created_at.asc(),
                                TaskEventModel.sequence.asc().nullslast(),
                                TaskEventModel.id.asc(),
                            )
                            .limit(200)
                        )
            else:
                # since_sequence == 0: replay events from the LATEST task only
                # (avoid flooding with entire session history from old tasks)
                # then only replay events from that specific task.
                latest_task_result = await db.execute(
                    select(TaskEventModel.task_id)
                    .where(TaskEventModel.session_id == conversation_id)
                    .order_by(TaskEventModel.created_at.desc())
                    .limit(1)
                )
                latest_task_id = latest_task_result.scalar()

                if latest_task_id:
                    events_result = await db.execute(
                        select(TaskEventModel)
                        .where(
                            TaskEventModel.session_id == conversation_id,
                            TaskEventModel.task_id == latest_task_id,
                        )
                        .order_by(
                            TaskEventModel.created_at.asc(),
                            TaskEventModel.sequence.asc().nullslast(),
                            TaskEventModel.id.asc(),
                        )
                        .limit(200)
                    )
                else:
                    # No events at all — return empty
                    events_result = await db.execute(
                        select(TaskEventModel)
                        .where(
                            TaskEventModel.session_id == conversation_id,
                        )
                        .order_by(
                            TaskEventModel.created_at.asc(),
                            TaskEventModel.sequence.asc().nullslast(),
                            TaskEventModel.id.asc(),
                        )
                        .limit(200)
                    )

            events = events_result.scalars().all()

            # No reversal heuristic needed anymore.
            if not events:
                return 0

            replayed = 0
            for evt in events:
                try:
                    await websocket.send_json({
                        "type": "event_replay",
                        "event_type": evt.event_type,
                        "data": evt.data or {},
                        "sequence": evt.sequence,
                        "task_id": str(evt.task_id) if evt.task_id else None,
                        "replayed": True,
                    })
                    replayed += 1
                except Exception:
                    break  # WS closed during replay

            if replayed:
                logger.info(
                    "ws_events_replayed",
                    conversation_id=conversation_id,
                    since_sequence=since_sequence,
                    replayed=replayed,
                )
            return replayed
    except Exception as e:
        logger.warning("ws_replay_failed", error=str(e), conversation_id=conversation_id)
        return 0


def _make_event_pusher(websocket: WebSocket, streaming: bool):
    """Create an event push callback bound to a specific WS connection and mode.

    SIMPLIFIED: No re-streaming on task_completed for late subscribers.
    Frontend loads persisted message from DB when it receives stream_end.
    This eliminates duplicate content bugs caused by re-streaming to
    reconnecting tabs that then also load from DB.
    """

    async def _ws_event_push(event: dict) -> None:
        """Push task events to WebSocket in real-time."""
        event_type = event.get("event_type", "unknown")
        try:
            # ── Token streaming (live LLM output) ──
            if event_type == "token" and streaming:
                token_content = (
                    event.get("data", {}).get("content", "")
                    or event.get("content", "")
                )
                if token_content:
                    await websocket.send_json({
                        "type": "stream_token",
                        "content": token_content,
                    })
                return

            # ── Token events in non-streaming mode: skip ──
            if event_type == "token" and not streaming:
                return

            # ── Task completed ──
            if event_type == "task_completed":
                final_message = event.get("data", {}).get("message", "")
                if streaming:
                    # Frontend will load the persisted message from DB.
                    await websocket.send_json({
                        "type": "stream_end",
                        "full_content": final_message,
                        "sequence": event.get("sequence"),
                        "usage": {},
                        "task_id": event.get("task_id"),
                        "agents_spawned": event.get("data", {}).get("agents_spawned", []),
                        "swarm_id": event.get("data", {}).get("swarm_id"),
                        "status": event.get("data", {}).get("status"),
                        "metadata": event.get("data", {}).get("metadata"),
                    })
                else:
                    await websocket.send_json({
                        "type": "complete",
                        "message": final_message,
                        "sequence": event.get("sequence"),
                        "task_id": event.get("task_id"),
                        "agents_spawned": event.get("data", {}).get("agents_spawned", []),
                        "swarm_id": event.get("data", {}).get("swarm_id"),
                        "status": event.get("data", {}).get("status"),
                        "metadata": event.get("data", {}).get("metadata"),
                    })
                return

            # ── Task failed ──
            if event_type == "task_failed":
                await websocket.send_json({
                    "type": "error",
                    "message": event.get("data", {}).get("error", "Task failed"),
                    "sequence": event.get("sequence"),
                    "task_id": event.get("task_id"),
                })
                return

            # ── Task cancelled ──
            if event_type == "task_cancelled":
                await websocket.send_json({
                    "type": "status",
                    "status": "cancelled",
                    "message": "Task was cancelled",
                    "sequence": event.get("sequence"),
                })
                return

            # ── Regular progress events ──
            # FIX: BUG-RT02 - Flatten event data for WS consistency with SSE.
            # SSE sends flat events; WS was wrapping in {data: FULL_EVENT} causing
            # frontend consumers to get undefined fields when expecting flat access.
            # Hoist key fields to top level while keeping `data` for backward compat.
            await websocket.send_json({
                "type": "event",
                "event_type": event_type,
                "data": event,
                "sequence": event.get("sequence"),
                # Hoisted fields for flat access (matching SSE format)
                "agent_name": event.get("agent_name"),
                "tool": event.get("tool"),
                "content": event.get("content"),
                "session_id": event.get("session_id"),
                "task_id": event.get("task_id"),
            })
        except Exception:
            pass  # WS may have closed — task continues anyway

    return _ws_event_push


@ws_router.websocket("/ws/chat/{conversation_id}")
async def websocket_chat(websocket: WebSocket, conversation_id: str):
    """
    WebSocket endpoint cho real-time chat.

    Architecture (Session Persistence fix + Hermes-agent patterns):
    - WS is a "window" into background tasks — NOT the execution context
    - Tasks run via TaskRunner, survive WS disconnect/reconnect
    - On reconnect: client uses replay or GET /api/v1/sessions/{id}/events
    - Multi-tab: multiple WS connections per conversation (all receive events)

    Supports message types:
    - {"type": "chat", "message": "...", "user_id": "..."}
      → Full orchestrator pipeline (non-streaming completion)
    - {"type": "chat_stream", "message": "...", "user_id": "..."}
      → Full orchestrator pipeline (streaming token delivery)
    - {"type": "subscribe"}
      → Subscribe to events for an already-running task (reconnect scenario)
    - {"type": "replay", "since_sequence": N}
      → Replay missed events since sequence N (reconnection catch-up)
    - {"type": "cancel"}
      → Cancel the currently running task for this session
    - {"type": "interrupt", "redirect_message": "..."}
      → Soft interrupt current execution (Hermes-agent pattern)
    - {"type": "steer", "message": "..."}
      → Inject guidance without interrupting (Hermes-agent pattern)

    Backward compatible — messages without "type" field default to "chat" mode.
    """
    # Extract optional metadata from query params
    tab_id = websocket.query_params.get("tab_id", "")
    user_id = websocket.query_params.get("user_id", "")

    # SEC-WS01: Validate JWT token if provided — extract trusted user_id
    token = websocket.query_params.get("token", "")
    if token:
        payload = verify_token(token)
        if payload:
            # Use JWT-verified user_id (overrides query param to prevent impersonation)
            user_id = payload.get("sub", "") or payload.get("user_id", "") or user_id
        else:
            # Invalid token — reject connection
            await websocket.close(code=4001, reason="Invalid or expired token")
            return
    # If no token provided, allow connection (dev mode / anonymous) but user_id
    # from query params is untrusted — only used for display, not authorization.

    await manager.connect(
        websocket,
        f"chat:{conversation_id}",
        user_id=user_id,
        tab_id=tab_id,
        conversation_id=conversation_id,
    )

    rate_limiter = WSRateLimiter(max_messages=20, window_seconds=60.0)
    runner = get_task_runner()

    # Track active subscription to avoid leaks on multiple messages
    _active_unsub: list = []  # list of one callable (mutable container for closure)

    _bg_unsub = None
    if user_id:
        try:
            from app.core.background_tasks import get_background_task_tracker

            tracker = get_background_task_tracker()

            async def _on_bg_notification(event: dict) -> None:
                """Deliver background task notifications to this WS."""
                try:
                    await websocket.send_json(event)
                except Exception:
                    pass

            _bg_unsub = tracker.subscribe_notifications(user_id, _on_bg_notification)
        except Exception:
            pass

    def _cleanup_subscription():
        """Unsubscribe previous event subscription if any."""
        if _active_unsub:
            try:
                _active_unsub[0]()
            except Exception:
                pass
            _active_unsub.clear()

    try:
        # This eliminates the event gap between get_active_task() and subscribe()
        # where events emitted in that window are lost.
        # Subscribing when no task is running is harmless (no events emitted).
        event_pusher = _make_event_pusher(websocket, streaming=True)
        unsub = runner.subscribe(conversation_id, event_pusher)
        _active_unsub.append(unsub)

        # This prevents the race condition where:
        # - Pusher receives task_completed → sends stream_end
        # - THEN status check also sends stream_end → duplicate
        # Frontend uses session_state to decide whether to load messages from DB
        # or subscribe to live events. Single consistent signal.
        active_task = runner.get_active_task(conversation_id)
        if active_task:
            await websocket.send_json({
                "type": "session_state",
                "status": "running",
                "task_id": active_task.task_id,
                "last_activity": active_task.last_activity_desc,
                "started_at": active_task.started_at.isoformat(),
                "tabs_connected": manager.get_conversation_tab_count(conversation_id),
            })
        else:
            last_task = runner.get_last_task(conversation_id)
            await websocket.send_json({
                "type": "session_state",
                "status": last_task.status if last_task else "idle",
                "task_id": last_task.task_id if last_task else None,
                "tabs_connected": manager.get_conversation_tab_count(conversation_id),
            })

        while True:
            try:
                # KEEPALIVE FIX: Use timeout so we can send heartbeats during
                # long LLM calls. Without this, external proxies (Cloudflare,
                # nginx gateways) close the connection after 30-60s of silence.
                data = await asyncio.wait_for(
                    websocket.receive_json(), timeout=250.0
                )
            except asyncio.TimeoutError:
                # No client message in 25s — send heartbeat to keep proxy alive
                try:
                    await websocket.send_json({
                        "type": "heartbeat",
                        "data": {"timestamp": int(time.time() * 1000)},
                    })
                except Exception:
                    break  # Connection dead — exit loop
                continue
            except (json.JSONDecodeError, ValueError):
                await websocket.send_json({
                    "type": "error",
                    "message": "Invalid JSON message. Please send valid JSON.",
                })
                continue

            if not rate_limiter.check():
                await websocket.send_json({
                    "type": "error",
                    "message": "Rate limited. Please wait before sending more messages.",
                })
                continue

            msg_type = data.get("type", "chat")

            # ── Handle subscribe request (reconnect scenario) ──
            if msg_type == "subscribe":
                active = runner.get_active_task(conversation_id)
                if active:
                    # BUG-15 FIX: Atomic swap — subscribe new BEFORE cleanup old.
                    # Events emitted between cleanup → new subscribe were lost.
                    event_pusher = _make_event_pusher(websocket, streaming=True)
                    unsub = runner.subscribe(conversation_id, event_pusher)
                    _cleanup_subscription()  # Clean old AFTER new is registered
                    _active_unsub.append(unsub)
                    await websocket.send_json({
                        "type": "subscribed",
                        "task_id": active.task_id,
                        "status": "running",
                        "tabs_connected": manager.get_conversation_tab_count(conversation_id),
                    })
                else:
                    await websocket.send_json({
                        "type": "subscribed",
                        "task_id": None,
                        "status": "idle",
                    })
                continue

            # ── Handle replay request (reconnection catch-up) ──
            if msg_type == "replay":
                since_sequence = data.get("since_sequence", 0)
                replayed = await _replay_missed_events(
                    websocket, conversation_id, since_sequence
                )
                await websocket.send_json({
                    "type": "replay_complete",
                    "events_replayed": replayed,
                    "since_sequence": since_sequence,
                })
                continue

            # ── Handle cancel request ──
            if msg_type == "cancel":
                cancelled = await runner.cancel_task(conversation_id)
                await websocket.send_json({
                    "type": "status",
                    "status": "cancelled" if cancelled else "no_task",
                    "message": "Task cancelled" if cancelled else "No active task to cancel",
                })
                continue

            # ── Handle interrupt request (Hermes-agent pattern) ──
            if msg_type == "interrupt":
                redirect_msg = data.get("redirect_message")
                from app.core.cancellation import interrupt_session
                await interrupt_session(conversation_id, redirect_message=redirect_msg)
                await websocket.send_json({
                    "type": "status",
                    "status": "interrupt_sent",
                    "message": "Interrupt signal sent to session",
                    "has_redirect": redirect_msg is not None,
                })
                # Notify other tabs in same conversation
                await manager.send_to_conversation(
                    conversation_id,
                    {
                        "type": "session_event",
                        "event": "interrupt",
                        "from_tab": tab_id,
                    },
                    exclude_ws=websocket,
                )
                continue

            # ── Handle steer request (Hermes-agent pattern) ──
            if msg_type == "steer":
                steer_msg = data.get("message", "")
                if steer_msg.strip():
                    from app.core.cancellation import steer_session
                    await steer_session(conversation_id, steer_msg)
                    await websocket.send_json({
                        "type": "status",
                        "status": "steer_queued",
                        "message": "Steering guidance queued for agent",
                    })
                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Empty steer message",
                    })
                continue

            # ── Handle chat messages (both streaming and non-streaming) ──
            message = data.get("message", "")
            # Always use the authenticated user_id from WS query params (set at connect time).
            # Previously: msg_user_id = data.get("user_id", user_id or "default_user")
            # This allowed clients to impersonate other users by sending
            # {"type": "chat_stream", "message": "...", "user_id": "admin"}.
            msg_user_id = user_id or "default_user"
            streaming = msg_type == "chat_stream"

            if not message.strip():
                await websocket.send_json({
                    "type": "error",
                    "message": "Empty message",
                })
                continue

            # Long message length validation
            from app.config import get_settings as _get_ws_settings
            _ws_settings = _get_ws_settings()
            if len(message) > _ws_settings.long_message_max_chars:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Message exceeds maximum length ({_ws_settings.long_message_max_chars:,} chars)",
                })
                continue

            # === SESSION MUTEX: Prevent race conditions when multiple
            # messages arrive concurrently for the same conversation ===
            mutex = get_session_mutex()
            unlock = await mutex.acquire_with_timeout(conversation_id, timeout=100.0)
            if unlock is None:
                await websocket.send_json({
                    "type": "error",
                    "message": "Session busy. Please wait and try again.",
                    "code": "SESSION_LOCKED",
                })
                continue

            try:
                # If there's already a running task, check before cancelling
                active = runner.get_active_task(conversation_id)
                if active:
                    # (swarm debates, complex research) when user sends a new message.
                    # Only cancel short tasks (< 30s = simple chat responses).
                    _active_age = (datetime.now(timezone.utc) - active.started_at).total_seconds()
                    if _active_age > 30:
                        # Long-running task — inform user instead of cancelling
                        await websocket.send_json({
                            "type": "error",
                            "message": (
                                f"⚠️ A task is still running ({int(_active_age)}s). "
                                "Please wait for it to complete or click the Stop button to cancel it. "
                                "Your message will be processed after the current task finishes."
                            ),
                            "code": "TASK_BUSY",
                            "active_task_id": active.task_id,
                        })
                        continue  # Skip processing this message

                    # SHORT TASK — safe to interrupt and cancel.
                    # then poll until task is actually cleared. Previous approach used
                    # interrupt_session + sleep(0.5) + cancel which raced with start_task's
                    # own check (start_task would see task still "running" → RuntimeError).
                    await runner.cancel_task(conversation_id)
                    # CancelledError can take >3s to propagate through deep asyncio stacks
                    # (e.g., inside LLM call with retry, or during debate synthesis).
                    # The old 3s window was insufficient for complex cancellation paths,
                    # causing start_task() to see stale task → RuntimeError "task is still running".
                    for _wait_i in range(50):  # 50 × 100ms = 5s
                        await asyncio.sleep(0.1)
                        _check_status = runner.get_task_status(conversation_id)
                        if _check_status is None or _check_status != "running":
                            break

                # Persist user message IMMEDIATELY (crash-safe, separate DB session)
                # NOTE: Assistant message is persisted by TaskRunner (single persist path)
                await _persist_user_message_ws(conversation_id, message)

                try:
                    from app.core.conversation_titles import schedule_title_generation
                    schedule_title_generation(conversation_id, message)
                except Exception:
                    pass  # Best-effort, never block chat flow

                # Atomic swap pattern: ensures no event gap between old unsub and new sub.
                # Previously, events emitted between _cleanup_subscription() and subscribe()
                # were silently lost.
                event_pusher = _make_event_pusher(websocket, streaming)
                new_unsub = runner.subscribe(conversation_id, event_pusher)
                _cleanup_subscription()  # Clean old AFTER new is registered
                _active_unsub.append(new_unsub)

                # Start task in background — NON-BLOCKING
                # Task survives WS disconnect; receive loop continues for cancel/subscribe
                # race condition (previous cancel hasn't fully cleared yet).
                try:
                    _start_attempts = 2  # 1 try + 1 retry
                    _start_error = None
                    task_id = None
                    for _start_i in range(_start_attempts):
                        try:
                            task_id = await runner.start_task(conversation_id, msg_user_id, message)
                            break  # Success
                        except RuntimeError as _rt_err:
                            _start_error = _rt_err
                            if _start_i < _start_attempts - 1:
                                # Retry after short wait — previous cancel may still be clearing
                                await asyncio.sleep(0.3)
                            else:
                                raise
                    await websocket.send_json({
                        "type": "task_started",
                        "task_id": task_id,
                        "session_id": conversation_id,
                        "tabs_connected": manager.get_conversation_tab_count(conversation_id),
                    })
                    # Notify other tabs in same conversation
                    await manager.send_to_conversation(
                        conversation_id,
                        {
                            "type": "session_event",
                            "event": "task_started",
                            "task_id": task_id,
                            "session_id": conversation_id,
                            "from_tab": tab_id,
                        },
                        exclude_ws=websocket,
                    )
                except Exception as e:
                    # BUG-WS-SUB FIX: Do NOT cleanup subscription on start_task failure.
                    # If a background task is already running, cleaning up the subscriber
                    # leaves this WS with NO active event subscription — the user would
                    # never receive task_completed/task_failed events from the running task.
                    # The subscription remains active and will receive events from whatever
                    # task is currently running (if any). On next successful start_task,
                    # the atomic swap pattern above will replace it cleanly.
                    await websocket.send_json({"type": "error", "message": str(e)})
            finally:
                # === RELEASE SESSION MUTEX ===
                unlock()

    except WebSocketDisconnect:
        # Task continues in background — only clean up WS-specific resources
        _cleanup_subscription()
        if _bg_unsub:
            _bg_unsub()
        await manager.disconnect(websocket, f"chat:{conversation_id}")
        logger.info(
            "ws_disconnected",
            conversation_id=conversation_id,
            tab_id=tab_id,
            task_still_running=runner.get_active_task(conversation_id) is not None,
            remaining_tabs=manager.get_conversation_tab_count(conversation_id),
        )
    except Exception as e:
        _cleanup_subscription()
        if _bg_unsub:
            _bg_unsub()
        logger.error("ws_error", error=str(e))
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        # in ConnectionManager indefinitely on unhandled exceptions (not WebSocketDisconnect).
        await manager.disconnect(websocket, f"chat:{conversation_id}")


@ws_router.websocket("/ws/monitor")
async def websocket_monitor(websocket: WebSocket):
    """
    WebSocket endpoint cho system monitoring.

    Stream real-time events: agent spawn, task updates, swarm activity.
    Read-only metrics (CPU, memory, agent counts) — no auth required,
    consistent with REST API endpoints that use get_optional_user.

    AUDIT-M3 FIX: Events are now filtered by user_id from query params.
    Only events belonging to the user's own sessions are delivered.
    This prevents privacy leaks where User A could see User B's activity.
    If no user_id is provided (admin/debug), all events are forwarded.
    """
    # M-03 FIX: Validate JWT token — extract user_id from token (trusted).
    # If token is valid, filter events to user's own sessions.
    # If no token, still allow (dev mode) but with user_id from query param.
    monitor_user_id = websocket.query_params.get("user_id", "")
    token = websocket.query_params.get("token", "")
    if token:
        payload = verify_token(token)
        if payload:
            # Use JWT-verified user_id — ensures privacy (user sees only own events)
            monitor_user_id = payload.get("sub", "") or payload.get("user_id", "") or monitor_user_id
        else:
            # Invalid token — reject
            await websocket.close(code=4001, reason="Invalid or expired token")
            return

    await manager.connect(websocket, "monitor")
    rate_limiter = WSRateLimiter(max_messages=30, window_seconds=60.0)
    _subscribed = False  # Track subscription state for safe cleanup

    try:
        bus = await get_message_bus()

        # BUG-17 FIX: System events whitelist — these are forwarded regardless of user filter
        _SYSTEM_EVENT_TYPES = frozenset({
            "system_metric", "system_stats", "connection_stats", "heartbeat_stats",
        })

        async def on_event(event_data: dict[str, Any]):
            # Transform MessageBus envelope → frontend-expected format
            raw = event_data.get("payload", event_data)
            event_type = raw.get("event_type", raw.get("type", "unknown"))
            data = {k: v for k, v in raw.items() if k not in ("event_type", "type")}

            # If monitor_user_id is set, only forward events from that user's sessions.
            # Events carry user_id in payload (set by TaskRunner._broadcast_execution_event).
            # BUG-17 FIX: Allow system events through regardless of user filter.
            if event_type not in _SYSTEM_EVENT_TYPES and monitor_user_id:
                event_user = data.get("user_id", "")
                if not event_user or event_user != monitor_user_id:
                    return  # Skip: either anonymous event or belongs to different user

            try:
                await websocket.send_json({"type": event_type, "data": data})
            except Exception:
                pass  # Connection may have closed

        await bus.subscribe("nexus:events", on_event)
        _subscribed = True

        # Keep alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=300.0)

                if not rate_limiter.check():
                    await websocket.send_json({
                        "type": "error",
                        "message": "Rate limited. Please wait before sending more messages.",
                    })
                    continue

                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif data.get("type") == "stats":
                    # Return connection stats
                    await websocket.send_json({
                        "type": "connection_stats",
                        "data": manager.get_connection_stats(),
                    })
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "heartbeat",
                    "data": {"timestamp": int(time.time() * 1000)}
                })

    except WebSocketDisconnect:
        pass  # Normal disconnect
    except Exception as e:
        logger.error("monitor_ws_error", error=str(e))
    finally:
        # the finally block, preventing manager.disconnect() from running → connection leak.
        if _subscribed:
            try:
                await bus.unsubscribe("nexus:events", on_event)  # type: ignore[possibly-undefined]
            except Exception:
                pass
        await manager.disconnect(websocket, "monitor")
