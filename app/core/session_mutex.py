"""
MekongAI Nexus - Session Mutex for Concurrent Operations.

Inspired by Shannon's SessionMutex (FIFO queue-based async mutex).
Prevents race conditions when multiple WebSocket messages or background
tasks modify the same session state concurrently.

Key properties:
- FIFO ordering: requests are processed in arrival order
- Per-session granularity: different sessions don't block each other
- Async-native: no thread blocking, plays well with asyncio event loop
- Auto-cleanup: locks are removed when queue drains

Usage:
    from app.core.session_mutex import get_session_mutex

    mutex = get_session_mutex()
    unlock = await mutex.acquire("session-123")
    try:
        # Critical section — modify session state safely
        await update_session_data(session_id)
    finally:
        unlock()
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


class SessionMutex:
    """
    FIFO-ordered async mutex per session.

    Uses promise-chaining semantics: each acquire() chains onto the
    previous lock's completion future, guaranteeing strict FIFO ordering
    without starvation or deadlocks.

    Thread-safe for single-threaded asyncio (which is the norm).
    """

    def __init__(self, warn_wait_ms: float = 5000.0) -> None:
        """
        Initialize SessionMutex.

        Args:
            warn_wait_ms: Log warning if lock wait exceeds this duration (ms).
        """
        self._locks: dict[str, asyncio.Future[None]] = {}
        self._warn_wait_ms = warn_wait_ms
        self._stats: dict[str, int] = {}  # session_id → acquire count

    async def acquire(self, session_id: str) -> Callable[[], None]:
        """
        Acquire the mutex for a session. Returns an unlock function.

        FIFO guarantee: if A calls acquire() before B, A gets the lock first.
        B waits until A's unlock() is called.

        Args:
            session_id: The session to lock.

        Returns:
            A callable that releases the lock. MUST be called in finally block.
        """
        # 1. Capture current tail of the queue
        prev = self._locks.get(session_id)

        # 2. Create our lock future and become the new tail
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._locks[session_id] = future

        # 3. Wait for predecessor to release (if any)
        if prev is not None:
            start = time.monotonic()
            await prev
            wait_ms = (time.monotonic() - start) * 1000
            if wait_ms > self._warn_wait_ms:
                logger.warning(
                    "session_mutex_long_wait",
                    session_id=session_id,
                    wait_ms=round(wait_ms, 1),
                )

        # Track stats
        self._stats[session_id] = self._stats.get(session_id, 0) + 1

        # 4. Return unlock function that releases next waiter in chain
        def unlock() -> None:
            # Clean up if we're still the tail (no one queued after us)
            if self._locks.get(session_id) is future:
                del self._locks[session_id]
            # Release the next waiter
            if not future.done():
                future.set_result(None)

        return unlock

    async def acquire_with_timeout(
        self, session_id: str, timeout: float = 30.0
    ) -> Optional[Callable[[], None]]:
        """
        Acquire with timeout. Returns None if timeout exceeded.

        Useful for WebSocket handlers that shouldn't block indefinitely.

        DEADLOCK-FIX: On timeout, we MUST resolve the future we registered
        in self._locks to unblock any downstream waiters that chained after us.
        Previously, timeout left an unresolved future as the tail → permanent
        deadlock for that session_id.

        Args:
            session_id: The session to lock.
            timeout: Maximum wait time in seconds.

        Returns:
            Unlock function, or None if timed out.
        """
        # 1. Register our future as new tail BEFORE waiting
        prev = self._locks.get(session_id)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._locks[session_id] = future

        try:
            # 2. Wait for predecessor (if any) with timeout
            if prev is not None:
                await asyncio.wait_for(asyncio.shield(prev), timeout=timeout)
        except asyncio.CancelledError:
            # BUG-M3 FIX: External cancellation — don't break mutex chain.
            # Resolve our future so downstream waiters are unblocked.
            if self._locks.get(session_id) is future:
                del self._locks[session_id]
            if not future.done():
                future.set_result(None)
            raise
        except asyncio.TimeoutError:
            # CRITICAL: Resolve our future to unblock downstream waiters
            if self._locks.get(session_id) is future:
                del self._locks[session_id]
            if not future.done():
                future.set_result(None)
            logger.error(
                "session_mutex_timeout",
                session_id=session_id,
                timeout_s=timeout,
            )
            return None

        # 3. Lock acquired — track stats
        self._stats[session_id] = self._stats.get(session_id, 0) + 1

        # 4. Return unlock function
        def unlock() -> None:
            if self._locks.get(session_id) is future:
                del self._locks[session_id]
            if not future.done():
                future.set_result(None)

        return unlock

    @property
    def active_locks(self) -> int:
        """Number of sessions currently locked."""
        return len(self._locks)

    def is_locked(self, session_id: str) -> bool:
        """Check if a session is currently locked."""
        return session_id in self._locks

    def get_stats(self) -> dict[str, int]:
        """Get acquisition counts per session (for monitoring)."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Reset acquisition counters."""
        self._stats.clear()


# --- Module-level singleton ---

_global_mutex: Optional[SessionMutex] = None


def get_session_mutex() -> SessionMutex:
    """Get the global SessionMutex instance (lazy singleton)."""
    global _global_mutex
    if _global_mutex is None:
        _global_mutex = SessionMutex()
    return _global_mutex


def reset_session_mutex() -> None:
    """Reset global mutex (for testing)."""
    global _global_mutex
    _global_mutex = None
