"""
MekongAI Nexus - Token Streaming Adapter.

Adapter for streaming LLM tokens to WebSocket clients in real-time.
Bridges between LLM streaming interface and WebSocket event delivery.

Hermes-agent pattern: Token-by-token delivery for responsive UX.
User sees the agent "typing" in real-time, not waiting for full response.

Features:
- Token buffering (batch small tokens for efficiency)
- Rate limiting (avoid overwhelming slow WS connections)
- Structured events: token, tool_start, tool_progress, tool_done
- Support for multiple simultaneous subscribers (multi-tab)
- Graceful degradation if WS disconnects mid-stream
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)

# Configuration
TOKEN_BUFFER_SIZE = 3  # Batch N tokens before sending (reduces WS overhead)
TOKEN_FLUSH_INTERVAL = 0.05  # Max wait before flushing buffer (50ms)
MAX_TOKENS_PER_SECOND = 200  # Rate limit to avoid overwhelming client


@dataclass
class StreamingState:
    """Tracks the state of an active streaming session."""

    session_key: str
    started_at: float = field(default_factory=time.monotonic)
    tokens_sent: int = 0
    bytes_sent: int = 0
    tool_events_sent: int = 0
    is_active: bool = True
    last_token_at: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def tokens_per_second(self) -> float:
        elapsed = self.elapsed_seconds
        if elapsed == 0:
            return 0.0
        return self.tokens_sent / elapsed


class TokenStreamer:
    """
    Streams LLM tokens to WebSocket subscribers in real-time.

    Usage:
        streamer = TokenStreamer(session_key="user:conv")

        # Add subscriber (WebSocket send callback)
        streamer.add_subscriber(ws_send_callback)

        # Use as on_token callback for LLM streaming
        async for token in llm.chat_stream(messages):
            await streamer.emit_token(token)

        # Or pass streamer.emit_token directly to chat_streaming_with_tools
        result = await llm.chat_streaming_with_tools(
            messages, on_token=streamer.emit_token
        )

        # End stream
        await streamer.end_stream(full_content="...", usage={...})
    """

    def __init__(
        self,
        session_key: str,
        *,
        buffer_size: int = TOKEN_BUFFER_SIZE,
        flush_interval: float = TOKEN_FLUSH_INTERVAL,
    ) -> None:
        self._session_key = session_key
        self._buffer_size = buffer_size
        self._flush_interval = flush_interval
        self._buffer: list[str] = []
        self._subscribers: list[Callable] = []  # async callable(dict) -> None
        self._state = StreamingState(session_key=session_key)
        self._last_flush: float = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> StreamingState:
        return self._state

    @property
    def is_active(self) -> bool:
        return self._state.is_active

    def add_subscriber(self, callback: Callable) -> None:
        """Add a WebSocket subscriber callback."""
        self._subscribers.append(callback)

    def remove_subscriber(self, callback: Callable) -> None:
        """Remove a subscriber."""
        self._subscribers = [s for s in self._subscribers if s != callback]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def emit_token(self, token: str) -> None:
        """
        Buffer and emit a single token.

        Called by LLM streaming interface. Buffers tokens and flushes
        when buffer is full or flush interval expires.
        """
        if not self._state.is_active:
            return

        async with self._lock:
            self._buffer.append(token)
            self._state.tokens_sent += 1
            self._state.bytes_sent += len(token.encode("utf-8"))
            self._state.last_token_at = time.monotonic()

            # Flush if buffer full or time exceeded
            now = time.monotonic()
            should_flush = (
                len(self._buffer) >= self._buffer_size
                or (now - self._last_flush) >= self._flush_interval
            )

            if should_flush:
                await self._flush_buffer()

    async def flush(self) -> None:
        """Force flush any remaining buffered tokens."""
        async with self._lock:
            if self._buffer:
                await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Internal: send buffered tokens to all subscribers."""
        if not self._buffer:
            return

        # Combine buffered tokens
        content = "".join(self._buffer)
        self._buffer.clear()
        self._last_flush = time.monotonic()

        # Build event
        event = {
            "type": "stream_token",
            "content": content,
            "tokens_so_far": self._state.tokens_sent,
        }

        # Send to all subscribers (best-effort)
        await self._broadcast(event)

    async def emit_tool_start(
        self,
        tool_name: str,
        params: dict[str, Any],
        *,
        round_num: int = 0,
        agent_name: str = "",
    ) -> None:
        """Emit structured tool_start event."""
        # Flush any pending tokens first
        await self.flush()

        event = {
            "type": "tool_start",
            "tool": tool_name,
            "params": params,
            "round": round_num,
            "agent_name": agent_name,
            "timestamp": time.time(),
        }
        self._state.tool_events_sent += 1
        await self._broadcast(event)

    async def emit_tool_progress(
        self,
        tool_name: str,
        progress: str,
        *,
        percent: Optional[float] = None,
    ) -> None:
        """Emit tool_progress event (for long-running tools)."""
        event = {
            "type": "tool_progress",
            "tool": tool_name,
            "progress": progress,
            "percent": percent,
            "timestamp": time.time(),
        }
        self._state.tool_events_sent += 1
        await self._broadcast(event)

    async def emit_tool_done(
        self,
        tool_name: str,
        success: bool,
        output: str,
        *,
        execution_time_ms: float = 0.0,
        params: Optional[dict[str, Any]] = None,
    ) -> None:
        """Emit structured tool_done event with full output."""
        event = {
            "type": "tool_done",
            "tool": tool_name,
            "success": success,
            "output": output[:5000],  # Cap output for WS delivery
            "output_preview": output[:200],
            "execution_time_ms": round(execution_time_ms, 1),
            "params": params or {},
            "timestamp": time.time(),
        }
        self._state.tool_events_sent += 1
        await self._broadcast(event)

    async def end_stream(
        self,
        full_content: str = "",
        usage: Optional[dict[str, Any]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Signal end of streaming. Send final stream_end event."""
        # Flush remaining buffer
        await self.flush()

        self._state.is_active = False

        event = {
            "type": "stream_end",
            "full_content": full_content,
            "usage": usage or {},
            "metadata": metadata or {},
            "stats": {
                "tokens_sent": self._state.tokens_sent,
                "bytes_sent": self._state.bytes_sent,
                "tool_events": self._state.tool_events_sent,
                "elapsed_seconds": round(self._state.elapsed_seconds, 2),
                "tokens_per_second": round(self._state.tokens_per_second, 1),
            },
        }
        await self._broadcast(event)

    async def emit_error(self, error_message: str) -> None:
        """Emit an error event and end streaming."""
        await self.flush()
        self._state.is_active = False

        event = {
            "type": "stream_error",
            "message": error_message,
            "stats": {
                "tokens_sent": self._state.tokens_sent,
                "elapsed_seconds": round(self._state.elapsed_seconds, 2),
            },
        }
        await self._broadcast(event)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        """Send event to all subscribers (best-effort, never raises)."""
        dead_subscribers: list[Callable] = []

        for subscriber in self._subscribers:
            try:
                await subscriber(event)
            except Exception:
                dead_subscribers.append(subscriber)

        # Remove dead subscribers
        if dead_subscribers:
            self._subscribers = [
                s for s in self._subscribers if s not in dead_subscribers
            ]


# ── Factory for creating streamers per session ──

_active_streamers: dict[str, TokenStreamer] = {}
_streamer_lock = asyncio.Lock()


async def get_or_create_streamer(
    session_key: str,
    **kwargs: Any,
) -> TokenStreamer:
    """Get or create a TokenStreamer for a session (atomic, race-safe)."""
    async with _streamer_lock:
        if session_key in _active_streamers:
            existing = _active_streamers[session_key]
            if existing.is_active:
                return existing

        streamer = TokenStreamer(session_key, **kwargs)
        _active_streamers[session_key] = streamer
        return streamer


async def remove_streamer(session_key: str) -> Optional[TokenStreamer]:
    """Remove and return streamer for a session (atomic, race-safe)."""
    async with _streamer_lock:
        return _active_streamers.pop(session_key, None)


def get_active_streamers() -> dict[str, StreamingState]:
    """Get states of all active streamers (for monitoring)."""
    return {
        key: streamer.state
        for key, streamer in _active_streamers.items()
        if streamer.is_active
    }
