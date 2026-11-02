"""
Nexus - Plugin Hook System (OpenClaw-inspired, Enhanced).

Event-driven hook system allowing plugins to intercept and modify behavior
at every stage of the agent lifecycle.

Inspired by:
- OpenClaw src/hooks/ — lifecycle hooks with approval support
- OpenClaw AGENTS.md — before_dispatch, before_prompt_build, etc.
- OpenClaw plugins/hooks — requireApproval pattern

Features beyond original:
- BEFORE_DISPATCH: intercept before message routing (OpenClaw pattern)
- BEFORE_PROMPT_BUILD: modify system prompt before assembly (OpenClaw pattern)
- AFTER_COMPACTION: react to context compaction (OpenClaw pattern)
- BEFORE_RESET: cleanup before session reset (OpenClaw pattern)
- BEFORE_DELIVERY: modify outbound before channel send
- ON_MEMORY_FLUSH: react to memory write events
- require_approval: pause tool execution for human approval
- Hook timeout: prevent hung handlers from blocking agent
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, Optional

import structlog

logger = structlog.get_logger(__name__)

# Default timeout for hook handlers (seconds)
DEFAULT_HOOK_TIMEOUT = 30.0


class HookEvent(str, Enum):
    """Events plugins can subscribe to.

    Lifecycle order for a typical agent turn:
        BEFORE_DISPATCH → ON_MESSAGE_INGEST → BEFORE_PROMPT_BUILD →
        BEFORE_AGENT_REPLY → (BEFORE_TOOL_CALL → AFTER_TOOL_CALL)* →
        AFTER_AGENT_REPLY → BEFORE_DELIVERY

    Session lifecycle:
        ON_SESSION_START → ... → ON_COMPACT / AFTER_COMPACTION →
        BEFORE_RESET → ON_SESSION_END

    Memory lifecycle:
        ON_MEMORY_FLUSH → ON_MEMORY_RECALL
    """
    # ── Message routing ──
    BEFORE_DISPATCH = "before_dispatch"          # Before message routing (NEW - OpenClaw)
    ON_MESSAGE_INGEST = "on_message_ingest"      # After message received

    # ── Prompt assembly ──
    BEFORE_PROMPT_BUILD = "before_prompt_build"  # Before system prompt assembly (NEW - OpenClaw)

    # ── Agent reply ──
    BEFORE_AGENT_REPLY = "before_agent_reply"    # Before LLM call
    AFTER_AGENT_REPLY = "after_agent_reply"       # After LLM response

    # ── Tool execution ──
    BEFORE_TOOL_CALL = "before_tool_call"        # Before tool dispatch
    AFTER_TOOL_CALL = "after_tool_call"          # After tool result

    # ── Outbound delivery ──
    BEFORE_DELIVERY = "before_delivery"          # Before channel send (NEW)

    # ── Session lifecycle ──
    ON_SESSION_START = "on_session_start"
    ON_SESSION_END = "on_session_end"
    BEFORE_RESET = "before_reset"                # Before session reset (NEW - OpenClaw)

    # ── Context management ──
    ON_COMPACT = "on_compact"                    # Before compaction
    AFTER_COMPACTION = "after_compaction"         # After compaction done (NEW - OpenClaw)

    # ── Memory ──
    ON_MEMORY_FLUSH = "on_memory_flush"          # Memory written to disk (NEW)
    ON_MEMORY_RECALL = "on_memory_recall"        # Memory recalled/searched (NEW)

    # ── System ──
    ON_ERROR = "on_error"
    ON_PLUGIN_LOAD = "on_plugin_load"
    ON_PLUGIN_UNLOAD = "on_plugin_unload"        # Plugin removed (NEW)
    ON_HEALTH_CHECK = "on_health_check"          # Doctor/health probe (NEW)


class HookDecision(str, Enum):
    """Terminal decisions a hook handler can make."""
    CONTINUE = "continue"            # Default — proceed normally
    CANCEL = "cancel"                # Cancel the event (block)
    REQUIRE_APPROVAL = "require_approval"  # Pause for human approval (NEW - OpenClaw)
    SHORT_CIRCUIT = "short_circuit"  # Skip remaining handlers, use result


@dataclass
class HookRegistration:
    """A registered hook handler."""
    plugin_id: str
    event: HookEvent
    handler: Callable[..., Awaitable[Any]]
    priority: int = 100  # lower = runs first
    timeout: float = DEFAULT_HOOK_TIMEOUT


@dataclass
class HookContext:
    """Context passed to hook handlers. Handlers can mutate data.

    Enhanced with:
    - decision: terminal hook decisions (cancel, require_approval, short_circuit)
    - approval_reason: why approval is needed (for require_approval)
    - modified_data: track which fields were modified by hooks
    - timing: track handler execution time
    """
    event: HookEvent
    data: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
    decision: HookDecision = HookDecision.CONTINUE
    results: list[Any] = field(default_factory=list)
    approval_reason: str = ""
    modified_fields: set[str] = field(default_factory=set)
    handler_timings: list[dict[str, Any]] = field(default_factory=list)

    def cancel(self) -> None:
        """Cancel further processing of this event."""
        self.cancelled = True
        self.decision = HookDecision.CANCEL

    def require_approval(self, reason: str = "") -> None:
        """Request human approval before proceeding (OpenClaw pattern).

        Used in BEFORE_TOOL_CALL to pause dangerous tool execution.
        """
        self.decision = HookDecision.REQUIRE_APPROVAL
        self.approval_reason = reason
        self.cancelled = True  # Stop further hook processing

    def short_circuit(self, result: Any = None) -> None:
        """Skip remaining handlers and use provided result."""
        self.decision = HookDecision.SHORT_CIRCUIT
        if result is not None:
            self.results.append(result)
        self.cancelled = True

    def modify(self, key: str, value: Any) -> None:
        """Modify data and track the change."""
        self.data[key] = value
        self.modified_fields.add(key)

    @property
    def needs_approval(self) -> bool:
        return self.decision == HookDecision.REQUIRE_APPROVAL

    @property
    def is_short_circuited(self) -> bool:
        return self.decision == HookDecision.SHORT_CIRCUIT


class HookRunner:
    """
    Runs hooks in priority order with timeout protection.

    Enhanced from original with:
    - Per-handler timeout (prevents hung hooks from blocking)
    - require_approval decision support (OpenClaw pattern)
    - Handler timing tracking for diagnostics
    - short_circuit support for early returns
    - Fail-closed on handler crash (cancel event)

    Usage:
        runner = get_hook_runner()
        ctx = HookContext(event=HookEvent.BEFORE_TOOL_CALL,
                          data={"tool": "bash", "args": {...}})
        await runner.run(ctx)
        if ctx.needs_approval:
            # Route to approval UI (Telegram buttons, Discord, etc.)
            approved = await prompt_user_approval(ctx.approval_reason)
            if not approved:
                return  # Tool call blocked by user
        elif ctx.cancelled:
            # A plugin blocked this tool call
            ...
    """

    def __init__(self, fail_closed: bool = True) -> None:
        self._hooks: dict[HookEvent, list[HookRegistration]] = {e: [] for e in HookEvent}
        self._fail_closed = fail_closed
        self._stats = {"total_runs": 0, "total_handlers": 0, "errors": 0, "timeouts": 0}

    def register(self, plugin_id: str, event: HookEvent,
                 handler: Callable[..., Awaitable[Any]],
                 priority: int = 100,
                 timeout: float = DEFAULT_HOOK_TIMEOUT) -> None:
        """Register a hook handler for an event."""
        reg = HookRegistration(
            plugin_id=plugin_id, event=event,
            handler=handler, priority=priority, timeout=timeout,
        )
        self._hooks[event].append(reg)
        self._hooks[event].sort(key=lambda r: r.priority)
        logger.debug("hook_registered", plugin=plugin_id,
                      event=event.value, priority=priority)

    def unregister(self, plugin_id: str) -> int:
        """Remove all hooks for a plugin. Returns count removed."""
        removed = 0
        for event in HookEvent:
            before = len(self._hooks[event])
            self._hooks[event] = [h for h in self._hooks[event] if h.plugin_id != plugin_id]
            removed += before - len(self._hooks[event])
        if removed:
            logger.info("hooks_unregistered", plugin=plugin_id, count=removed)
        return removed

    async def run(self, ctx: HookContext) -> HookContext:
        """Run all handlers for the event in priority order.

        Respects handler timeouts, cancel/approval/short_circuit decisions,
        and tracks timing for diagnostics.
        """
        self._stats["total_runs"] += 1
        handlers = self._hooks.get(ctx.event, [])
        if not handlers:
            return ctx

        for reg in handlers:
            if ctx.cancelled:
                break

            self._stats["total_handlers"] += 1
            t0 = time.time()

            try:
                result = await asyncio.wait_for(
                    reg.handler(ctx),
                    timeout=reg.timeout,
                )
                dt = (time.time() - t0) * 1000
                ctx.handler_timings.append({
                    "plugin": reg.plugin_id,
                    "event": ctx.event.value,
                    "duration_ms": round(dt, 1),
                    "decision": ctx.decision.value,
                })
                if result is not None:
                    ctx.results.append(result)

            except asyncio.TimeoutError:
                dt = (time.time() - t0) * 1000
                self._stats["timeouts"] += 1
                logger.warning("hook_handler_timeout",
                               plugin=reg.plugin_id, event=ctx.event.value,
                               timeout=reg.timeout, duration_ms=round(dt, 1))
                if self._fail_closed:
                    ctx.cancel()

            except Exception:
                self._stats["errors"] += 1
                logger.exception("hook_handler_error",
                                 plugin=reg.plugin_id, event=ctx.event.value)
                if self._fail_closed:
                    ctx.cancel()

        return ctx

    def has_handlers(self, event: HookEvent) -> bool:
        """Check if any handlers registered for this event."""
        return len(self._hooks.get(event, [])) > 0

    def list_hooks(self) -> dict[str, list[str]]:
        """Return {event: [plugin_ids]} for introspection."""
        return {
            e.value: [r.plugin_id for r in regs]
            for e, regs in self._hooks.items()
            if regs
        }

    def get_stats(self) -> dict[str, int]:
        """Return hook execution statistics."""
        return dict(self._stats)

    def count_handlers(self) -> int:
        """Total registered handlers across all events."""
        return sum(len(regs) for regs in self._hooks.values())


_hook_runner: Optional[HookRunner] = None


def get_hook_runner() -> HookRunner:
    """Get the singleton HookRunner instance."""
    global _hook_runner
    if _hook_runner is None:
        _hook_runner = HookRunner()
    return _hook_runner
