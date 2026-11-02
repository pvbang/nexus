"""
Nexus - Task Cancellation & Session Interrupt Support.

Provides:
1. CancellationToken - cooperative cancellation for long-running tasks
2. Session-scoped interrupt - soft interrupt per session (Hermes-agent style)
3. Session steering - inject guidance without interrupting

Usage in orchestrator:
    token = register_token(str(task.id))
    for round_num in range(max_rounds):
        if token.is_cancelled:
            break
        ...
    unregister_token(str(task.id))

Usage from API:
    cancel_task(str(task_id))  # sets the cancellation flag

Session-level (Hermes-agent patterns):
    interrupt_session(session_id, redirect_message="New question from user")
    steer_session(session_id, steer_message="Focus on security aspects")
    
    # In react loop:
    redirect = check_session_interrupt(session_id)
    if redirect is not None:
        # Gracefully stop and handle redirect
    
    steer_msgs = consume_steer_messages(session_id)
    if steer_msgs:
        # Inject as context for next LLM call
"""

import asyncio
import time as _time
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class CancellationToken:
    """Thread-safe cancellation token for long-running tasks."""

    def __init__(self) -> None:
        self._cancelled = asyncio.Event()

    def cancel(self) -> None:
        """Signal cancellation."""
        self._cancelled.set()

    @property
    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancelled.is_set()

    async def wait_cancelled(self, timeout: float | None = None) -> bool:
        """Wait until cancelled or timeout.

        Returns True if cancelled, False if timed out.
        """
        try:
            await asyncio.wait_for(self._cancelled.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


# ══════════════════════════════════════════════════════════════════════
# Task-level cancellation registry
# ══════════════════════════════════════════════════════════════════════

# Global registry of active task cancellation tokens
_active_tokens: dict[str, CancellationToken] = {}
# AUDIT-FIX: Track registration time for TTL-based cleanup of orphaned tokens
_token_registered_at: dict[str, float] = {}
_TOKEN_TTL_SECONDS = 7200.0  # 2 hours — tokens older than this are considered orphaned


def register_token(task_id: str) -> CancellationToken:
    """Register a new cancellation token for a task.

    Returns the token that can be checked during execution.
    AUDIT-FIX: Also records registration time for TTL cleanup.
    """
    # Opportunistic cleanup: remove orphaned tokens (> TTL)
    _cleanup_expired_tokens()

    token = CancellationToken()
    _active_tokens[task_id] = token
    _token_registered_at[task_id] = _time.time()
    logger.debug("cancellation_token_registered", task_id=task_id)
    return token


def cancel_task(task_id: str) -> bool:
    """Cancel a running task by setting its cancellation token.

    Returns True if the token was found and cancelled, False otherwise.
    """
    token = _active_tokens.get(task_id)
    if token:
        token.cancel()
        logger.info("task_cancelled", task_id=task_id)
        return True
    logger.warning("cancel_token_not_found", task_id=task_id)
    return False


def unregister_token(task_id: str) -> None:
    """Remove a cancellation token after task completes."""
    removed = _active_tokens.pop(task_id, None)
    _token_registered_at.pop(task_id, None)
    if removed:
        logger.debug("cancellation_token_unregistered", task_id=task_id)


def get_token(task_id: str) -> Optional[CancellationToken]:
    """Get the cancellation token for a task, if it exists."""
    return _active_tokens.get(task_id)


def _cleanup_expired_tokens() -> None:
    """AUDIT-FIX: Remove tokens older than TTL (orphaned from crashed tasks).

    Called opportunistically on each register_token() to avoid accumulation.
    Only cleans up 10 tokens max per call to avoid blocking.
    """
    now = _time.time()
    expired: list[str] = []
    for task_id, registered_at in _token_registered_at.items():
        if (now - registered_at) > _TOKEN_TTL_SECONDS:
            expired.append(task_id)
        if len(expired) >= 10:
            break  # Limit cleanup per call

    for task_id in expired:
        _active_tokens.pop(task_id, None)
        _token_registered_at.pop(task_id, None)

    if expired:
        logger.info("expired_tokens_cleaned", count=len(expired), remaining=len(_active_tokens))


# ══════════════════════════════════════════════════════════════════════
# Session-level interrupt & steering (Hermes-agent patterns)
# ══════════════════════════════════════════════════════════════════════


class SessionInterruptState:
    """Per-session interrupt and steering state.

    Interrupt: Soft stop — agent checks between tool calls, gracefully
    halts current execution, optionally processes redirect message.

    Steer: Guidance injection — agent continues running but receives
    additional context/instructions at next checkpoint.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._interrupt_event = asyncio.Event()
        self._interrupt_message: Optional[str] = None
        self._steer_messages: list[str] = []
        self._lock = asyncio.Lock()

    @property
    def is_interrupted(self) -> bool:
        """Check if interrupt has been requested."""
        return self._interrupt_event.is_set()

    async def set_interrupt(self, redirect_message: Optional[str] = None) -> None:
        """Set the interrupt flag with optional redirect message."""
        async with self._lock:
            self._interrupt_message = redirect_message
            self._interrupt_event.set()

    async def consume_interrupt(self) -> Optional[str]:
        """Consume the interrupt (one-shot). Returns redirect message or ''."""
        async with self._lock:
            if not self._interrupt_event.is_set():
                return None
            msg = self._interrupt_message
            self._interrupt_event.clear()
            self._interrupt_message = None
            return msg if msg is not None else ""

    async def add_steer(self, message: str) -> None:
        """Queue a steering message."""
        async with self._lock:
            self._steer_messages.append(message)

    async def consume_steers(self) -> list[str]:
        """Consume all pending steer messages. Returns list (empty if none)."""
        async with self._lock:
            if not self._steer_messages:
                return []
            msgs = self._steer_messages[:]
            self._steer_messages.clear()
            return msgs

    @property
    def has_steers(self) -> bool:
        """Check if there are pending steer messages (non-blocking)."""
        return len(self._steer_messages) > 0


# Global registry of session interrupt states
_session_states: dict[str, SessionInterruptState] = {}
_session_state_access: dict[str, float] = {}  # M4-FIX: track last access time
_SESSION_STATE_TTL = 3600  # 1 hour TTL for unused states
_LAST_CLEANUP_TIME: float = 0.0  # MEDIUM-1 FIX: time-based cleanup trigger
_CLEANUP_INTERVAL: float = 300.0  # Run cleanup every 5 minutes


def _get_or_create_session_state(session_id: str) -> SessionInterruptState:
    """Get or create interrupt state for a session."""
    global _LAST_CLEANUP_TIME
    import time
    now = time.monotonic()

    # MEDIUM-1 FIX: Time-based cleanup trigger (every 5 minutes).
    # Previous approach (every 100 accesses) failed under low traffic
    # because stale entries accumulated indefinitely.
    if (now - _LAST_CLEANUP_TIME) > _CLEANUP_INTERVAL:
        _cleanup_stale_session_states(now)
        _LAST_CLEANUP_TIME = now

    if session_id not in _session_states:
        _session_states[session_id] = SessionInterruptState(session_id)
    _session_state_access[session_id] = now
    return _session_states[session_id]


def _cleanup_stale_session_states(now: float) -> None:
    """Remove session states not accessed in the last TTL period."""
    stale = [
        sid for sid, last_access in _session_state_access.items()
        if (now - last_access) > _SESSION_STATE_TTL
    ]
    for sid in stale:
        _session_states.pop(sid, None)
        _session_state_access.pop(sid, None)


async def interrupt_session(
    session_id: str,
    redirect_message: Optional[str] = None,
) -> bool:
    """
    Soft interrupt a running session.

    The agent checks this flag between tool calls and gracefully stops.
    Optionally, a redirect message can be provided for the agent to
    process instead of the original task.

    Args:
        session_id: Session/conversation ID to interrupt.
        redirect_message: Optional message for agent after interruption.

    Returns:
        True always (interrupt is set regardless of whether session is active).
    """
    state = _get_or_create_session_state(session_id)
    await state.set_interrupt(redirect_message)
    logger.info(
        "session_interrupt_set",
        session_id=session_id,
        has_redirect=redirect_message is not None,
    )
    return True


async def steer_session(session_id: str, steer_message: str) -> bool:
    """
    Inject a steering note into a running session WITHOUT interrupting.

    The steer message is queued and injected as additional context at the
    next checkpoint in the react loop. The agent sees it as guidance but
    continues its current execution.

    Args:
        session_id: Session/conversation ID to steer.
        steer_message: Guidance/instruction to inject.

    Returns:
        True always (steer is queued regardless of session state).
    """
    state = _get_or_create_session_state(session_id)
    await state.add_steer(steer_message)
    logger.info(
        "session_steer_queued",
        session_id=session_id,
        message_preview=steer_message[:100],
    )
    return True


async def check_session_interrupt(session_id: str) -> Optional[str]:
    """
    Check if a session has a pending interrupt.

    Called by the react loop between tool calls. Returns:
    - None: No interrupt pending → continue execution
    - "": Interrupt without redirect → stop gracefully
    - "message": Interrupt with redirect → stop and process redirect

    Consumes the interrupt (one-shot).
    """
    state = _session_states.get(session_id)
    if state is None:
        return None
    return await state.consume_interrupt()


async def consume_steer_messages(session_id: str) -> list[str]:
    """
    Consume all pending steer messages for a session.

    Called by the react loop at checkpoints. Returns empty list if none.
    Clears the queue after reading.
    """
    state = _session_states.get(session_id)
    if state is None:
        return []
    return await state.consume_steers()


def has_pending_steers(session_id: str) -> bool:
    """Non-async check if session has pending steer messages."""
    state = _session_states.get(session_id)
    if state is None:
        return False
    return state.has_steers


def cleanup_session_state(session_id: str) -> None:
    """Remove interrupt state for a session (on session end)."""
    removed = _session_states.pop(session_id, None)
    if removed:
        logger.debug("session_interrupt_state_cleaned", session_id=session_id)


def cancel_all_for_session(session_id: str) -> int:
    """
    Cancel ALL active tasks for a given session.

    PHASE-3 FIX: Actually iterates tokens and cancels them.
    Previously this function was broken — it never cancelled any tokens,
    only set the session interrupt event. Now it:
    1. Gets the TaskRunner to find task_id for this session
    2. Cancels the cancellation token for that task
    3. Cancels the asyncio task via TaskRunner
    4. Sets session interrupt as fallback

    Returns count of tasks cancelled.
    """
    cancelled = 0

    # 1. Find and cancel via TaskRunner's public API (has session_id → task mapping)
    # BUG-M7 FIX: Use get_active_task() instead of accessing _running_tasks directly.
    # Direct dict access bypasses any lock protection and breaks encapsulation.
    try:
        from app.core.task_runner import get_task_runner
        runner = get_task_runner()
        task = runner.get_active_task(session_id)
        if task:
            # Cancel via cooperative token
            token = _active_tokens.get(task.task_id)
            if token and not token.is_cancelled:
                token.cancel()
                cancelled += 1
            # Cancel asyncio task
            if task.asyncio_task and not task.asyncio_task.done():
                task.asyncio_task.cancel()
    except Exception as _e:
        logger.debug("cancel_all_task_runner_error", error=str(_e))

    # 2. Set session-level interrupt as fallback
    state = _get_or_create_session_state(session_id)
    state._interrupt_event.set()
    state._interrupt_message = None

    logger.info(
        "session_cancel_all",
        session_id=session_id,
        cancelled=cancelled,
    )
    return cancelled
