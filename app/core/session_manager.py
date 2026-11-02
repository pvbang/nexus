"""
Nexus - Session Manager.

Session isolation + Agent caching per session (Hermes-agent inspired).
Central coordination point for session lifecycle management.

Features:
- Session key format: {user_id}:{conversation_id}
- Cache orchestrator instances per session (avoid re-creating per message)
- TTL-based eviction (session idle 30 minutes → evict)
- Thread-safe with asyncio.Lock
- Integration with IterationBudget and CompressionChain
- Interrupt/steer support per session
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from app.core.agent_cache import AgentCache, get_agent_cache
from app.core.compression_chain import CompressionChain, get_or_create_chain
from app.core.iteration_budget import (
    IterationBudget,
    get_or_create_budget,
    remove_budget,
)

logger = structlog.get_logger(__name__)


@dataclass
class SessionState:
    """Mutable state associated with a session."""

    session_key: str
    user_id: str
    conversation_id: str
    created_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    message_count: int = 0
    is_active: bool = True

    # Interrupt/steer state (Hermes-agent pattern)
    interrupt_requested: bool = False
    interrupt_message: Optional[str] = None
    steer_messages: list[str] = field(default_factory=list)

    # References to per-session components
    budget: Optional[IterationBudget] = None
    compression_chain: Optional[CompressionChain] = None
    ref_count: int = 0  # Active requests using this session

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = time.monotonic()
        self.message_count += 1

    @property
    def idle_seconds(self) -> float:
        """Seconds since last activity."""
        return time.monotonic() - self.last_activity

    @property
    def age_seconds(self) -> float:
        """Total session age in seconds."""
        return time.monotonic() - self.created_at


def make_session_key(user_id: str, conversation_id: str) -> str:
    """Create canonical session key from user and conversation IDs."""
    return f"{user_id}:{conversation_id}"


class SessionManager:
    """
    Central session lifecycle manager.

    Coordinates:
    - Session creation/eviction
    - Agent cache (orchestrator instances)
    - Iteration budgets per session
    - Compression chains per session
    - Interrupt/steer signals per session

    Usage:
        mgr = get_session_manager()

        # Get or create session (returns existing if alive)
        session = await mgr.get_or_create_session(user_id, conversation_id)

        # Access session components
        budget = session.budget
        chain = session.compression_chain

        # Interrupt a session (from API/WS handler)
        await mgr.interrupt_session(session_key, redirect_message="User asked new question")

        # End session explicitly
        await mgr.end_session(session_key)
    """

    def __init__(
        self,
        max_sessions: int = 500,  # MEDIUM-3 FIX: Raised from 100 → 500 for multi-user production
        session_ttl_seconds: float = 1800.0,  # 30 minutes
        default_budget_iterations: int = 0,  # 0 = use settings default
    ) -> None:
        self._max_sessions = max_sessions
        self._session_ttl = session_ttl_seconds
        self._default_budget_iterations = default_budget_iterations
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()
        self._agent_cache: AgentCache[Any] = get_agent_cache(
            max_size=max_sessions,
            ttl_seconds=session_ttl_seconds,
        )

    @property
    def active_session_count(self) -> int:
        """Number of active sessions."""
        return len(self._sessions)

    @property
    def agent_cache(self) -> AgentCache[Any]:
        """Access the underlying agent cache."""
        return self._agent_cache

    async def get_or_create_session(
        self,
        user_id: str,
        conversation_id: str,
        *,
        budget_iterations: int = 0,
    ) -> SessionState:
        """
        Get existing session or create a new one.

        If session exists and is not expired, returns it (with updated activity).
        If expired or not found, creates fresh session with new budget/chain.

        Args:
            user_id: User identifier.
            conversation_id: Conversation identifier.
            budget_iterations: Override iteration budget. 0 = use default.

        Returns:
            SessionState for this session.
        """
        session_key = make_session_key(user_id, conversation_id)

        async with self._lock:
            existing = self._sessions.get(session_key)

            if existing is not None:
                # Check TTL
                if existing.idle_seconds > self._session_ttl:
                    # Expired — clean up and create fresh
                    await self._evict_session_unlocked(session_key)
                else:
                    # Active session — touch and return
                    existing.touch()
                    return existing

            # Evict oldest if at capacity
            if len(self._sessions) >= self._max_sessions:
                await self._evict_oldest_unlocked()

            # Create new session
            budget_max = budget_iterations or self._default_budget_iterations
            budget = get_or_create_budget(session_key, max_iterations=budget_max)

            compression_chain = await get_or_create_chain(session_key)

            session = SessionState(
                session_key=session_key,
                user_id=user_id,
                conversation_id=conversation_id,
                budget=budget,
                compression_chain=compression_chain,
            )
            self._sessions[session_key] = session

            logger.info(
                "session_created",
                session_key=session_key,
                user_id=user_id,
                conversation_id=conversation_id,
                budget_max=budget.max_iterations,
            )
            return session

    async def get_session(self, session_key: str) -> Optional[SessionState]:
        """Get session by key without creating. Returns None if not found/expired."""
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return None
            if session.idle_seconds > self._session_ttl:
                await self._evict_session_unlocked(session_key)
                return None
            return session

    async def acquire_session(self, session_key: str) -> None:
        """Increment ref_count to prevent eviction during active use."""
        async with self._lock:
            session = self._sessions.get(session_key)
            if session:
                session.ref_count += 1

    async def release_session(self, session_key: str) -> None:
        """Decrement ref_count after request completes."""
        async with self._lock:
            session = self._sessions.get(session_key)
            if session:
                session.ref_count = max(0, session.ref_count - 1)

    async def end_session(self, session_key: str) -> bool:
        """
        Explicitly end a session and clean up resources.

        Returns True if session was found and ended.
        """
        async with self._lock:
            return await self._evict_session_unlocked(session_key)

    async def interrupt_session(
        self,
        session_key: str,
        redirect_message: Optional[str] = None,
    ) -> bool:
        """
        Soft interrupt a running session.

        Sets interrupt flag so the react loop/orchestrator checks it between
        tool calls and gracefully stops. Optionally provides a redirect message
        for the agent to process instead.

        Args:
            session_key: Session to interrupt.
            redirect_message: Optional message for the agent after interruption.

        Returns:
            True if session was found and interrupt was set.
        """
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return False

            session.interrupt_requested = True
            session.interrupt_message = redirect_message

            logger.info(
                "session_interrupted",
                session_key=session_key,
                has_redirect=redirect_message is not None,
            )
            return True

    async def steer_session(
        self,
        session_key: str,
        steer_message: str,
    ) -> bool:
        """
        Inject a steering note into a running session WITHOUT interrupting.

        The steer message is queued and injected as context at the next
        checkpoint in the react loop. The agent sees it as additional
        user guidance but continues its current execution.

        Args:
            session_key: Session to steer.
            steer_message: Guidance message to inject.

        Returns:
            True if session was found and steer was queued.
        """
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return False

            session.steer_messages.append(steer_message)

            logger.info(
                "session_steered",
                session_key=session_key,
                steer_queue_size=len(session.steer_messages),
            )
            return True

    async def check_interrupt(self, session_key: str) -> Optional[str]:
        """
        Check if session has a pending interrupt.

        Returns the redirect message (or empty string if interrupt without message).
        Returns None if no interrupt pending.

        Clears the interrupt flag after reading (one-shot).
        """
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return None

            if not session.interrupt_requested:
                return None

            # Consume interrupt (one-shot)
            redirect = session.interrupt_message or ""
            session.interrupt_requested = False
            session.interrupt_message = None
            return redirect

    async def consume_steer_messages(self, session_key: str) -> list[str]:
        """
        Consume all pending steer messages for a session.

        Returns list of steer messages (empty if none pending).
        Clears the queue after reading.
        """
        async with self._lock:
            session = self._sessions.get(session_key)
            if session is None:
                return []

            if not session.steer_messages:
                return []

            messages = session.steer_messages[:]
            session.steer_messages.clear()
            return messages

    async def cleanup_expired(self) -> int:
        """
        Remove all expired sessions. Returns count evicted.

        Should be called periodically (e.g., every 5 minutes).

        BUG-M2 FIX: agent_cache.cleanup_expired() moved inside lock to prevent
        concurrent session creation from racing with cache eviction.
        """
        async with self._lock:
            expired_keys: list[str] = []
            for key, session in self._sessions.items():
                if session.idle_seconds > self._session_ttl:
                    expired_keys.append(key)

            for key in expired_keys:
                await self._evict_session_unlocked(key)

            if expired_keys:
                logger.info(
                    "sessions_cleanup_expired",
                    evicted=len(expired_keys),
                    remaining=len(self._sessions),
                )

            # Cleanup agent cache within lock to prevent race with session creation
            await self._agent_cache.cleanup_expired()

        return len(expired_keys)

    async def get_all_sessions(self) -> list[dict[str, Any]]:
        """Get summary of all active sessions (for monitoring)."""
        async with self._lock:
            result: list[dict[str, Any]] = []
            for key, session in self._sessions.items():
                result.append({
                    "session_key": key,
                    "user_id": session.user_id,
                    "conversation_id": session.conversation_id,
                    "message_count": session.message_count,
                    "idle_seconds": round(session.idle_seconds, 1),
                    "age_seconds": round(session.age_seconds, 1),
                    "is_active": session.is_active,
                    "interrupt_pending": session.interrupt_requested,
                    "steer_queue_size": len(session.steer_messages),
                    "budget_remaining": (
                        session.budget.remaining if session.budget else None
                    ),
                })
            return result

    # ── Private helpers ──

    async def _evict_session_unlocked(self, session_key: str) -> bool:
        """Evict a session (must be called within _lock)."""
        session = self._sessions.pop(session_key, None)
        if session is None:
            return False

        session.is_active = False

        # Cleanup associated resources
        remove_budget(session_key)
        await self._agent_cache.evict(session_key)

        # Remove compression chain
        from app.core.compression_chain import remove_chain
        await remove_chain(session_key)

        # Remove session interrupt/steer state (prevents memory leak in _session_states dict)
        from app.core.cancellation import cleanup_session_state
        cleanup_session_state(session.conversation_id)

        # Remove token streamer (prevents memory leak in _active_streamers dict)
        from app.core.token_streamer import remove_streamer
        await remove_streamer(session_key)

        logger.info(
            "session_evicted",
            session_key=session_key,
            age_seconds=round(session.age_seconds, 1),
            message_count=session.message_count,
        )
        return True

    async def _evict_oldest_unlocked(self) -> None:
        """Evict the session with longest idle time (must be called within _lock).

        Skips sessions with active references (ref_count > 0).
        """
        if not self._sessions:
            return

        # Only consider sessions with no active references
        evictable = [
            k for k, s in self._sessions.items()
            if s.ref_count <= 0
        ]
        if not evictable:
            logger.warning("all_sessions_in_use_cannot_evict", total=len(self._sessions))
            return

        oldest_key = min(
            evictable,
            key=lambda k: self._sessions[k].last_activity,
        )
        await self._evict_session_unlocked(oldest_key)


# ── Module-level singleton ──

_global_session_manager: Optional[SessionManager] = None


def get_session_manager(
    max_sessions: int = 500,  # MEDIUM-3 FIX: Match SessionManager.__init__ default (500 for multi-user production)
    session_ttl_seconds: float = 1800.0,
) -> SessionManager:
    """Get or create the global session manager singleton."""
    global _global_session_manager
    if _global_session_manager is None:
        _global_session_manager = SessionManager(
            max_sessions=max_sessions,
            session_ttl_seconds=session_ttl_seconds,
        )
    return _global_session_manager
