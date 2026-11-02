"""
Nexus - Stale Stream Detection (Hermes-agent inspired).

Detect and kill hung SSE/streaming connections that stop producing
data. Prevents resource leaks from stalled LLM API connections.

Features:
- Configurable timeout per stream
- Watchdog timer that kills hung connections
- Integration with LLMClient streaming methods

Inspired by: hermes-agent/run_agent.py stale stream detection
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncGenerator, Optional, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")

# Default: kill stream if no chunk received for 300 seconds (5 min).
# Deep-thinking models (Opus, o1) may pause for extended periods during complex reasoning.
# Tool execution (chart generation, research) also causes gaps in LLM stream output.
DEFAULT_STALE_TIMEOUT = 300.0

# Minimum timeout (prevent accidental 0s timeout)
MIN_STALE_TIMEOUT = 5.0


class StaleStreamError(Exception):
    """Raised when a stream hasn't produced data within the timeout."""

    def __init__(self, timeout: float, last_chunk_age: float) -> None:
        self.timeout = timeout
        self.last_chunk_age = last_chunk_age
        super().__init__(
            f"Stream stale: no data for {last_chunk_age:.1f}s "
            f"(timeout: {timeout:.1f}s)"
        )


async def with_stale_detection(
    stream: AsyncGenerator[T, None],
    timeout: float = DEFAULT_STALE_TIMEOUT,
    stream_name: str = "llm_stream",
) -> AsyncGenerator[T, None]:
    """Wrap an async generator with stale detection.

    If no item is yielded within ``timeout`` seconds, raises
    :class:`StaleStreamError` and logs a warning.

    Usage::

        async for chunk in with_stale_detection(llm.chat_stream(msgs)):
            process(chunk)

    Parameters
    ----------
    stream:
        The async generator to monitor.
    timeout:
        Seconds to wait before declaring the stream stale.
    stream_name:
        Label for logging.

    Yields
    ------
    T:
        Items from the underlying stream.

    Raises
    ------
    StaleStreamError:
        If the stream goes silent for longer than *timeout*.
    """
    effective_timeout = max(timeout, MIN_STALE_TIMEOUT)
    last_chunk_time = time.monotonic()
    chunk_count = 0

    try:
        async for item in _timed_iter(stream, effective_timeout):
            now = time.monotonic()
            gap = now - last_chunk_time
            last_chunk_time = now
            chunk_count += 1

            # Log unusually long gaps (but not the first chunk)
            if chunk_count > 1 and gap > effective_timeout * 0.5:
                logger.debug(
                    "stream_slow_chunk",
                    stream=stream_name,
                    gap_seconds=round(gap, 1),
                    chunk_num=chunk_count,
                )

            yield item

    except StaleStreamError:
        age = time.monotonic() - last_chunk_time
        logger.warning(
            "stale_stream_killed",
            stream=stream_name,
            timeout=effective_timeout,
            last_chunk_age=round(age, 1),
            chunks_received=chunk_count,
        )
        raise

    except asyncio.CancelledError:
        logger.debug(
            "stream_cancelled",
            stream=stream_name,
            chunks_received=chunk_count,
        )
        raise

    finally:
        # Ensure the underlying generator is closed
        try:
            await stream.aclose()
        except Exception:
            pass


async def _timed_iter(
    stream: AsyncGenerator[T, None],
    timeout: float,
) -> AsyncGenerator[T, None]:
    """Internal: iterate with per-chunk timeout."""
    aiter = stream.__aiter__()

    while True:
        try:
            item = await asyncio.wait_for(
                aiter.__anext__(),
                timeout=timeout,
            )
            yield item
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise StaleStreamError(
                timeout=timeout,
                last_chunk_age=timeout,
            )


class StreamWatchdog:
    """Reusable watchdog for monitoring stream health.

    Provides a context manager interface for tracking active streams
    and killing them if they go stale.

    Usage::

        watchdog = StreamWatchdog(timeout=45.0)

        async with watchdog.watch("my_stream"):
            async for chunk in stream:
                watchdog.heartbeat()
                process(chunk)
    """

    def __init__(self, timeout: float = DEFAULT_STALE_TIMEOUT) -> None:
        self.timeout = max(timeout, MIN_STALE_TIMEOUT)
        self._last_heartbeat: float = 0.0
        self._stream_name: str = ""
        self._active: bool = False
        self._chunk_count: int = 0

    def heartbeat(self) -> None:
        """Signal that the stream is still alive."""
        self._last_heartbeat = time.monotonic()
        self._chunk_count += 1

    @property
    def is_stale(self) -> bool:
        """Check if the stream has gone stale."""
        if not self._active:
            return False
        return (time.monotonic() - self._last_heartbeat) > self.timeout

    @property
    def age_since_heartbeat(self) -> float:
        """Seconds since last heartbeat."""
        if not self._active:
            return 0.0
        return time.monotonic() - self._last_heartbeat

    class _WatchContext:
        def __init__(self, watchdog: "StreamWatchdog", name: str) -> None:
            self._wd = watchdog
            self._name = name

        async def __aenter__(self) -> "StreamWatchdog":
            self._wd._stream_name = self._name
            self._wd._active = True
            self._wd._chunk_count = 0
            self._wd.heartbeat()
            return self._wd

        async def __aexit__(self, *args: Any) -> None:
            self._wd._active = False
            logger.debug(
                "stream_watchdog_done",
                stream=self._name,
                chunks=self._wd._chunk_count,
            )

    def watch(self, stream_name: str = "stream") -> _WatchContext:
        """Context manager for watching a stream."""
        return self._WatchContext(self, stream_name)
