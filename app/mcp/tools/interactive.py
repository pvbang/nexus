"""
Interactive Decision Making Tool.

Allows agent to pause execution and ask user for input when facing:
- Ambiguous requirements
- Multiple valid options
- Missing critical information
- Risky actions requiring confirmation

Uses contextvars to access session_id and event_callback from the
orchestrator execution context (set in process_user_request).
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

import structlog

from app.core.execution_context import current_event_callback, current_session_id
from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

# ============================================================
# Pending Decisions Store (in-memory)
# ============================================================


class PendingDecision:
    """Represents a decision waiting for user input."""

    def __init__(
        self,
        session_id: str,
        question: str,
        options: list[dict[str, Any]],
        allow_custom: bool = False,
        timeout_seconds: int = 60,
        context: str = "",
    ):
        self.id = str(uuid.uuid4())
        self.session_id = session_id
        self.question = question
        self.options = sorted(options, key=lambda x: x.get("priority", 999))
        self.allow_custom = allow_custom
        self.timeout_seconds = timeout_seconds
        self.context = context
        self.created_at = datetime.utcnow()
        self.expires_at = self.created_at + timedelta(seconds=timeout_seconds)
        self.status: Literal["pending", "answered", "timeout"] = "pending"
        self.answer: str | None = None
        self._event = asyncio.Event()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "question": self.question,
            "options": self.options,
            "allow_custom": self.allow_custom,
            "timeout_seconds": self.timeout_seconds,
            "context": self.context,
            "created_at": self.created_at.isoformat() + "Z",
            "expires_at": self.expires_at.isoformat() + "Z",
            "status": self.status,
        }

    async def wait_for_answer(self) -> str:
        # H4-FIX: Watchdog ensures we never block forever even if event loop is degraded.
        # Also catches CancelledError to auto-resolve with default option.
        try:
            await asyncio.wait_for(self._event.wait(), timeout=self.timeout_seconds)
            if self.answer:
                return self.answer
            return self.options[0]["value"]
        except asyncio.TimeoutError:
            logger.info(
                "decision_timeout",
                decision_id=self.id,
                auto_selected=self.options[0]["value"],
            )
            self.status = "timeout"
            self.answer = self.options[0]["value"]
            return self.answer
        except asyncio.CancelledError:
            logger.info(
                "decision_cancelled",
                decision_id=self.id,
                auto_selected=self.options[0]["value"],
            )
            self.status = "cancelled"
            self.answer = self.options[0]["value"]
            return self.answer

    def set_answer(self, answer: str) -> None:
        self.answer = answer
        self.status = "answered"
        self._event.set()


# Global store
_pending_decisions: dict[str, PendingDecision] = {}


def get_pending_decision(decision_id: str) -> PendingDecision | None:
    return _pending_decisions.get(decision_id)


# Grace period after resolution before cleanup (allows frontend to catch up)
_CLEANUP_GRACE_SECONDS = 120


def cleanup_expired_decisions() -> None:
    """Remove decisions that have been resolved AND past grace period after expiry.

    Also cleans up pending decisions that expired without being answered (zombie state).
    """
    now = datetime.utcnow()
    grace_cutoff = now - timedelta(seconds=_CLEANUP_GRACE_SECONDS)
    expired = [
        did for did, dec in _pending_decisions.items()
        if (dec.status != "pending" and dec.expires_at < grace_cutoff)
        or (dec.status == "pending" and dec.expires_at < now)
    ]
    for did in expired:
        dec = _pending_decisions[did]
        if dec.status == "pending":
            dec.status = "timeout"
        del _pending_decisions[did]


# ============================================================
# Tool Handler — matches (params: dict, **kwargs) → dict
# ============================================================


async def handle_ask_user(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """
    Tool handler for ask_user. Called by ToolExecutor.

    Reads session_id and event_callback from contextvars
    (set by orchestrator in process_user_request).
    """
    question = params.get("question", "")
    options = params.get("options", [])
    allow_custom = params.get("allow_custom", False)
    timeout_seconds = params.get("timeout_seconds", 60)
    context = params.get("context", "")

    if not question or not options:
        return {
            "success": False,
            "output": "ask_user requires 'question' and 'options' (at least 2)",
            "data": None,
            "artifacts": [],
        }

    if len(options) < 2:
        return {
            "success": False,
            "output": "ask_user requires at least 2 options",
            "data": None,
            "artifacts": [],
        }

    # Validate options
    for opt in options:
        if "value" not in opt or "label" not in opt:
            return {
                "success": False,
                "output": "Each option must have 'value' and 'label'",
                "data": None,
                "artifacts": [],
            }
        if "priority" not in opt:
            opt["priority"] = 999

    # Get context from contextvars
    session_id = current_session_id.get()
    event_callback = current_event_callback.get()

    if not session_id:
        logger.warning("ask_user_no_session_id")
        # Fallback: auto-select first option
        return {
            "success": True,
            "output": f"Auto-selected: {options[0]['label']} (no active session)",
            "data": {"selected": options[0]["value"], "method": "auto_no_session"},
            "artifacts": [],
        }

    # Create pending decision
    decision = PendingDecision(
        session_id=session_id,
        question=question,
        options=options,
        allow_custom=allow_custom,
        timeout_seconds=min(timeout_seconds, 300),  # Max 5 minutes
        context=context,
    )
    _pending_decisions[decision.id] = decision

    logger.info(
        "decision_created",
        decision_id=decision.id,
        session_id=session_id,
        question=question[:100],
        options_count=len(options),
    )

    # Emit SSE event to frontend
    if event_callback:
        try:
            await event_callback({
                "type": "user_input_required",
                "event_type": "user_input_required",
                "decision_id": decision.id,
                "question": question,
                "options": decision.options,
                "allow_custom": allow_custom,
                "timeout_seconds": decision.timeout_seconds,
                "context": context,
                "expires_at": decision.expires_at.isoformat() + "Z",
            })
        except Exception as e:
            logger.error("decision_event_emit_failed", error=str(e))

    # Wait for user answer (or timeout → auto-select first)
    answer = await decision.wait_for_answer()

    logger.info(
        "decision_answered",
        decision_id=decision.id,
        answer=answer,
        status=decision.status,
    )

    # Emit resolution event so frontend knows agent resumed
    if event_callback:
        try:
            await event_callback({
                "type": "user_input_resolved",
                "event_type": "user_input_resolved",
                "decision_id": decision.id,
                "answer": answer,
                "method": decision.status,  # "answered" or "timeout"
            })
        except Exception:
            pass

    cleanup_expired_decisions()

    # Find label for selected answer
    selected_label = answer
    for opt in decision.options:
        if opt["value"] == answer:
            selected_label = opt["label"]
            break

    return {
        "success": True,
        "output": f"User selected: {selected_label}",
        "data": {
            "selected": answer,
            "label": selected_label,
            "method": decision.status,
        },
        "artifacts": [],
    }


# ============================================================
# Tool Schema
# ============================================================

ASK_USER_TOOL_SCHEMA = {
    "name": "ask_user",
    "description": (
        "Pause execution and ask user to choose from options when you need clarification. "
        "Use this when: (1) Requirements are ambiguous with multiple valid interpretations, "
        "(2) Multiple valid approaches exist and user preference matters, "
        "(3) Missing critical information only user can provide, "
        "(4) Risky action needs explicit confirmation. "
        "DO NOT overuse — only ask when truly necessary. Options are sorted by priority "
        "(1=recommended). If user doesn't respond within timeout, the first option (priority=1) "
        "is auto-selected.\n\n"
        "PRIORITY RULES:\n"
        "- Priority=1: The DEEPEST, most practical action you can execute AUTONOMOUSLY. "
        "Prefer options that DO something concrete (setup lab, run test, create PoC, build environment) "
        "over passive options (just research, just read). The user hired you to ACT, not just read.\n"
        "- Priority=2+: Less autonomous options or ones needing user input.\n"
        "- NEVER put 'research only' or 'just analyze' as priority=1 if you can instead "
        "setup a real environment, write code, or test something hands-on.\n"
        "- If you can create a Docker lab, write exploit code, build a test environment "
        "— that option MUST be priority=1."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "Clear, concise question for user",
            },
            "options": {
                "type": "array",
                "description": (
                    "2-5 options sorted by priority (1=best default). "
                    "Priority=1 MUST be an option the agent can execute autonomously "
                    "without needing further user input. Options requiring user to provide "
                    "data (URLs, paths, keys) should have higher priority numbers."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "description": "Return value if selected"},
                        "label": {"type": "string", "description": "Display label"},
                        "description": {"type": "string", "description": "Brief explanation"},
                        "priority": {
                            "type": "integer",
                            "description": (
                                "1=highest priority (auto-selected on timeout). "
                                "Priority 1 must be an autonomous action the agent can do alone."
                            ),
                            "minimum": 1,
                        },
                    },
                    "required": ["value", "label", "priority"],
                },
                "minItems": 2,
                "maxItems": 5,
            },
            "allow_custom": {
                "type": "boolean",
                "description": "Allow user to type a custom answer",
                "default": False,
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Auto-select first option after N seconds (default 60)",
                "default": 60,
                "minimum": 10,
                "maximum": 300,
            },
            "context": {
                "type": "string",
                "description": "Why you need to ask (shown to user)",
            },
        },
        "required": ["question", "options"],
    },
}

# ============================================================
# Registration
# ============================================================

registry.register(
    name="ask_user",
    toolset="interactive",
    category="user_interaction",
    schema=ASK_USER_TOOL_SCHEMA,
    handler=handle_ask_user,
    is_async=True,
    description="Pause and ask user for input when clarification needed",
    emoji="❓",
)
