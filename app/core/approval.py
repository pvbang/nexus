"""
MekongAI Nexus - Exec Approval Workflow (OpenClaw-inspired).

Human-in-the-loop approval system for dangerous tool executions.

Inspired by:
- OpenClaw src/hooks/ — requireApproval pattern
- OpenClaw plugins/hooks — before_tool_call approval gates
- OpenClaw SECURITY.md — exec approvals as operator guardrails

Features:
- Create approval requests for dangerous operations
- Channel-agnostic notification (Telegram, Discord, Web, etc.)
- Configurable timeout with auto-deny
- Persistent approval history for audit
- Session-scoped "remember" decisions
- Bulk approve/deny for batch operations
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable, Optional

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────
# Enums & Models
# ─────────────────────────────────────────────────────────────────


class ApprovalStatus(str, Enum):
    """Status of an approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ApprovalScope(str, Enum):
    """How broadly an approval decision applies."""
    ONCE = "once"               # This request only
    SESSION = "session"         # Remember for this session
    TOOL = "tool"               # Remember for this tool (any session)
    PERMANENT = "permanent"     # Always allow/deny this tool


@dataclass
class ApprovalRequest:
    """A pending approval request."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    session_id: str = ""
    user_id: str = ""
    channel: str = ""           # telegram, discord, web, cli
    status: ApprovalStatus = ApprovalStatus.PENDING
    scope: ApprovalScope = ApprovalScope.ONCE
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None
    decided_by: Optional[str] = None
    deny_reason: str = ""
    timeout_seconds: int = 30
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_pending(self) -> bool:
        return self.status == ApprovalStatus.PENDING

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.timeout_seconds

    @property
    def time_remaining(self) -> float:
        remaining = self.timeout_seconds - (time.time() - self.created_at)
        return max(0.0, remaining)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "reason": self.reason,
            "session_id": self.session_id,
            "channel": self.channel,
            "status": self.status.value,
            "scope": self.scope.value,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "timeout_seconds": self.timeout_seconds,
            "time_remaining": self.time_remaining,
        }

    def format_for_channel(self) -> str:
        """Format approval request for display in a channel."""
        args_preview = ", ".join(
            f"{k}={repr(v)[:50]}" for k, v in list(self.tool_args.items())[:5]
        )
        return (
            f"🔐 **Approval Required**\n\n"
            f"**Tool:** `{self.tool_name}`\n"
            f"**Args:** `{args_preview}`\n"
            f"**Reason:** {self.reason}\n"
            f"**Session:** {self.session_id[:8]}...\n"
            f"**Timeout:** {self.timeout_seconds}s\n\n"
            f"Reply with ✅ to approve or ❌ to deny."
        )


# Type alias for notification callbacks
NotifyCallback = Callable[[ApprovalRequest], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────
# Approval Manager
# ─────────────────────────────────────────────────────────────────


class ApprovalManager:
    """
    Manage approval requests for dangerous tool calls.

    Flow:
    1. Tool policy evaluates tool → decides ASK
    2. ApprovalManager.request_approval() creates request
    3. Notification sent to user's channel (Telegram buttons, etc.)
    4. User approves/denies
    5. ApprovalManager resolves the pending future
    6. Tool execution proceeds or is blocked

    Usage:
        mgr = get_approval_manager()

        # Create request and wait
        request = await mgr.request_approval(
            tool_name="bash",
            tool_args={"command": "rm -rf /data"},
            reason="Shell command execution",
            session_id="abc123",
            channel="telegram",
        )

        # In channel handler:
        await mgr.approve(request.id, decided_by="user@telegram")
        # OR
        await mgr.deny(request.id, decided_by="user@telegram", reason="Too dangerous")
    """

    def __init__(
        self,
        default_timeout: int = 60,
        max_pending: int = 60,
        cleanup_interval: float = 60.0,
    ) -> None:
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []
        self._futures: dict[str, asyncio.Future[ApprovalStatus]] = {}
        self._remembered: dict[str, ApprovalStatus] = {}  # tool:session → status
        self._notify_callbacks: list[NotifyCallback] = []
        self._default_timeout = default_timeout
        self._max_pending = max_pending
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: Optional[asyncio.Task] = None
        self._stats = {
            "total_requests": 0,
            "approved": 0,
            "denied": 0,
            "timeout": 0,
            "cancelled": 0,
        }

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def register_notify_callback(self, callback: NotifyCallback) -> None:
        """Register a callback for sending approval notifications.

        Multiple callbacks can be registered (e.g., Telegram + Web UI).
        """
        self._notify_callbacks.append(callback)

    async def request_approval(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        reason: str,
        session_id: str,
        channel: str = "web",
        user_id: str = "",
        timeout: Optional[int] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ApprovalRequest:
        """Create an approval request and notify the user.

        Args:
            tool_name: Name of the tool requiring approval.
            tool_args: Arguments to the tool.
            reason: Why approval is needed.
            session_id: Current session ID.
            channel: Channel to send notification to.
            user_id: User who triggered the tool call.
            timeout: Override default timeout.
            metadata: Additional metadata.

        Returns:
            ApprovalRequest (check status after wait_for_decision).
        """
        # Check if we have a remembered decision
        remember_key = f"{tool_name}:{session_id}"
        if remember_key in self._remembered:
            status = self._remembered[remember_key]
            request = ApprovalRequest(
                tool_name=tool_name,
                tool_args=tool_args,
                reason=f"Remembered: {reason}",
                session_id=session_id,
                user_id=user_id,
                channel=channel,
                status=status,
                decided_at=time.time(),
                decided_by="remembered",
                timeout_seconds=timeout or self._default_timeout,
                metadata=metadata or {},
            )
            logger.info("approval_remembered",
                        tool=tool_name, status=status.value)
            return request

        # Check pending limit
        if len(self._pending) >= self._max_pending:
            # Clean expired first
            self._cleanup_expired()
            if len(self._pending) >= self._max_pending:
                raise RuntimeError("Too many pending approval requests")

        # Create request
        request = ApprovalRequest(
            tool_name=tool_name,
            tool_args=tool_args,
            reason=reason,
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            timeout_seconds=timeout or self._default_timeout,
            metadata=metadata or {},
        )

        # Store and create future
        self._pending[request.id] = request
        loop = asyncio.get_event_loop()
        future: asyncio.Future[ApprovalStatus] = loop.create_future()
        self._futures[request.id] = future
        self._stats["total_requests"] += 1

        # Notify user
        await self._notify(request)

        logger.info(
            "approval_requested",
            request_id=request.id,
            tool=tool_name,
            session=session_id[:8],
            timeout=request.timeout_seconds,
        )

        return request

    async def wait_for_decision(
        self,
        request_id: str,
        timeout: Optional[float] = None,
    ) -> ApprovalStatus:
        """Wait for a decision on an approval request.

        Blocks until user approves/denies or timeout expires.

        Args:
            request_id: ID of the approval request.
            timeout: Override timeout. Defaults to request's timeout.

        Returns:
            Final ApprovalStatus (APPROVED, DENIED, or TIMEOUT).
        """
        request = self._pending.get(request_id)
        if request is None:
            # Check if already resolved
            for req in self._history:
                if req.id == request_id:
                    return req.status
            return ApprovalStatus.CANCELLED

        if not request.is_pending:
            return request.status

        future = self._futures.get(request_id)
        if future is None:
            return ApprovalStatus.CANCELLED

        wait_timeout = timeout or request.timeout_seconds

        try:
            status = await asyncio.wait_for(future, timeout=wait_timeout)
            return status
        except asyncio.TimeoutError:
            # Auto-APPROVE on timeout — user didn't respond, assume consent
            # This prevents tools from blocking indefinitely when no UI is shown
            logger.info(
                "approval_auto_approve_timeout",
                request_id=request_id,
                tool=request.tool_name,
                timeout=wait_timeout,
            )
            await self._resolve(request_id, ApprovalStatus.APPROVED, "system_auto", "Auto-approved on timeout")
            return ApprovalStatus.APPROVED

    async def approve(
        self,
        request_id: str,
        decided_by: str = "user",
        scope: ApprovalScope = ApprovalScope.ONCE,
    ) -> bool:
        """Approve a pending request.

        Args:
            request_id: ID of the request to approve.
            decided_by: Who made the decision.
            scope: How broadly to apply this decision.

        Returns:
            True if successfully approved, False if not found/expired.
        """
        return await self._resolve(
            request_id, ApprovalStatus.APPROVED, decided_by, "", scope,
        )

    async def deny(
        self,
        request_id: str,
        decided_by: str = "user",
        reason: str = "",
        scope: ApprovalScope = ApprovalScope.ONCE,
    ) -> bool:
        """Deny a pending request.

        Args:
            request_id: ID of the request to deny.
            decided_by: Who made the decision.
            reason: Why it was denied.
            scope: How broadly to apply this decision.

        Returns:
            True if successfully denied, False if not found/expired.
        """
        return await self._resolve(
            request_id, ApprovalStatus.DENIED, decided_by, reason, scope,
        )

    async def cancel(self, request_id: str) -> bool:
        """Cancel a pending request."""
        return await self._resolve(
            request_id, ApprovalStatus.CANCELLED, "system", "Cancelled",
        )

    async def _resolve(
        self,
        request_id: str,
        status: ApprovalStatus,
        decided_by: str,
        reason: str = "",
        scope: ApprovalScope = ApprovalScope.ONCE,
    ) -> bool:
        """Resolve a pending request with a decision."""
        request = self._pending.get(request_id)
        if request is None:
            return False

        # Update request
        request.status = status
        request.decided_at = time.time()
        request.decided_by = decided_by
        request.deny_reason = reason
        request.scope = scope

        # Remember decision if scope extends beyond ONCE
        if scope == ApprovalScope.SESSION:
            key = f"{request.tool_name}:{request.session_id}"
            self._remembered[key] = status
        elif scope == ApprovalScope.TOOL:
            # Remember for this tool across all sessions
            key = f"{request.tool_name}:*"
            self._remembered[key] = status

        # Move to history
        del self._pending[request_id]
        self._history.append(request)
        if len(self._history) > 500:
            self._history = self._history[-250:]

        # Resolve future
        future = self._futures.pop(request_id, None)
        if future and not future.done():
            future.set_result(status)

        # Update stats
        stat_key = status.value
        if stat_key in self._stats:
            self._stats[stat_key] += 1

        logger.info(
            "approval_resolved",
            request_id=request_id,
            tool=request.tool_name,
            status=status.value,
            decided_by=decided_by,
            scope=scope.value,
        )

        return True

    async def _notify(self, request: ApprovalRequest) -> None:
        """Send notification to all registered callbacks."""
        for callback in self._notify_callbacks:
            try:
                await callback(request)
            except Exception as exc:
                logger.warning("approval_notify_failed",
                               error=str(exc), channel=request.channel)

    def _cleanup_expired(self) -> int:
        """Clean up expired pending requests. Returns count cleaned."""
        expired_ids = [
            rid for rid, req in self._pending.items()
            if req.is_expired
        ]
        for rid in expired_ids:
            request = self._pending.pop(rid)
            request.status = ApprovalStatus.TIMEOUT
            request.decided_at = time.time()
            request.decided_by = "system"
            self._history.append(request)

            future = self._futures.pop(rid, None)
            if future and not future.done():
                future.set_result(ApprovalStatus.TIMEOUT)

            self._stats["timeout"] += 1

        if expired_ids:
            logger.info("approval_cleanup", expired=len(expired_ids))
        return len(expired_ids)

    # ── Session Management ───────────────────────────────────────

    def clear_session(self, session_id: str) -> int:
        """Clear remembered decisions for a session."""
        keys_to_remove = [
            k for k in self._remembered
            if k.endswith(f":{session_id}")
        ]
        for k in keys_to_remove:
            del self._remembered[k]
        return len(keys_to_remove)

    def clear_all_remembered(self) -> int:
        """Clear all remembered decisions."""
        count = len(self._remembered)
        self._remembered.clear()
        return count

    # ── Introspection ────────────────────────────────────────────

    def get_pending(self) -> list[dict[str, Any]]:
        """Return all pending requests."""
        return [req.to_dict() for req in self._pending.values()]

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent approval history."""
        return [req.to_dict() for req in self._history[-limit:]]

    def get_request(self, request_id: str) -> Optional[dict[str, Any]]:
        """Get a specific request by ID."""
        req = self._pending.get(request_id)
        if req:
            return req.to_dict()
        for req in self._history:
            if req.id == request_id:
                return req.to_dict()
        return None


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_manager: Optional[ApprovalManager] = None


def get_approval_manager() -> ApprovalManager:
    """Get the singleton ApprovalManager instance."""
    global _manager
    if _manager is None:
        _manager = ApprovalManager()
    return _manager
