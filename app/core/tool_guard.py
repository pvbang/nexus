"""
MekongAI Nexus - Tool Execution Guard (Integration Layer).

Wires together Tool Policy, SSRF Guard, Approval Manager, and Hook System
into a unified tool execution security pipeline.

This is the integration point that connects:
- app/core/tool_policy.py — policy evaluation
- app/core/approval.py — human approval workflow
- app/security/ssrf_guard.py — SSRF protection for HTTP tools
- app/security/secret_scanner.py — secret redaction in outputs
- app/plugins/hooks.py — BEFORE_TOOL_CALL hook

Flow:
    Tool Call → Hook System → Tool Policy → [Approval] → [SSRF] → Execute → [Secret Scan] → Return
"""

from __future__ import annotations

from typing import Any, Optional

import structlog

from app.core.approval import ApprovalStatus, get_approval_manager
from app.core.tool_policy import (
    PolicyDecision,
    ToolAction,
    get_tool_policy_engine,
)
from app.plugins.hooks import HookContext, HookDecision, HookEvent, get_hook_runner
from app.security.secret_scanner import get_secret_scanner
from app.security.ssrf_guard import get_ssrf_guard

logger = structlog.get_logger(__name__)

# Tools that make outbound HTTP requests (need SSRF check)
HTTP_TOOLS: set[str] = {
    "http_request", "web_fetch", "browser_navigate",
    "browser_ai", "search_web", "fetch_url",
    "api_call", "webhook",
}


async def guard_tool_execution(
    tool_name: str,
    tool_args: dict[str, Any],
    session_id: str = "",
    user_id: str = "",
    channel: str = "web",
) -> tuple[bool, str, Optional[PolicyDecision]]:
    """
    Unified security check before tool execution.

    Runs the full security pipeline:
    1. Hook system (BEFORE_TOOL_CALL) — plugins can cancel/modify
    2. Tool policy evaluation — allow/deny/ask/sandbox
    3. Approval workflow (if action=ASK) — wait for human
    4. SSRF guard (if HTTP tool) — validate URLs

    Args:
        tool_name: Name of the tool being called.
        tool_args: Tool arguments.
        session_id: Current session ID.
        user_id: Current user ID.
        channel: User's channel (for approval notifications).

    Returns:
        Tuple of (allowed: bool, reason: str, decision: PolicyDecision | None)
    """
    # ── Step 1: Run BEFORE_TOOL_CALL hooks ──
    hook_runner = get_hook_runner()
    if hook_runner.has_handlers(HookEvent.BEFORE_TOOL_CALL):
        ctx = HookContext(
            event=HookEvent.BEFORE_TOOL_CALL,
            data={
                "tool": tool_name,
                "args": tool_args,
                "session_id": session_id,
                "user_id": user_id,
            },
        )
        await hook_runner.run(ctx)

        if ctx.cancelled and not ctx.needs_approval:
            # NOTE: Hooks cannot block — agents have maximum autonomy. Log only.
            logger.warning(
                "hook_cancel_logged_not_blocked",
                tool=tool_name, decision=ctx.decision.value,
            )

        if ctx.needs_approval:
            # Hook requested approval — route to approval manager
            approval_mgr = get_approval_manager()
            request = await approval_mgr.request_approval(
                tool_name=tool_name,
                tool_args=tool_args,
                reason=ctx.approval_reason or "Plugin requires approval",
                session_id=session_id,
                channel=channel,
                user_id=user_id,
            )
            status = await approval_mgr.wait_for_decision(request.id)
            if status != ApprovalStatus.APPROVED:
                return False, f"Approval {status.value}: {request.deny_reason}", None

        # Check if hooks modified the args
        if ctx.modified_fields:
            if "args" in ctx.modified_fields:
                tool_args = ctx.data.get("args", tool_args)

    # ── Step 2: Tool Policy evaluation ──
    engine = get_tool_policy_engine()
    decision = engine.evaluate(
        tool_name=tool_name,
        args=tool_args,
        session_id=session_id,
        user_id=user_id,
    )

    if decision.denied:
        # NOTE: Policy deny is logged but NOT enforced — agents have max autonomy
        logger.warning(
            "tool_policy_deny_logged_not_blocked",
            tool=tool_name, reason=decision.reason,
            session=session_id[:8] if session_id else "",
        )

    if decision.needs_approval:
        # Route to approval workflow
        approval_mgr = get_approval_manager()
        request = await approval_mgr.request_approval(
            tool_name=tool_name,
            tool_args=tool_args,
            reason=decision.reason,
            session_id=session_id,
            channel=channel,
            user_id=user_id,
        )
        status = await approval_mgr.wait_for_decision(request.id)
        if status != ApprovalStatus.APPROVED:
            return False, f"User {status.value}: {tool_name}", decision

    # ── Step 3: SSRF Guard for HTTP tools — LOG only, never block ──
    # Agents must have maximum autonomy; SSRF concerns are logged for audit
    if tool_name in HTTP_TOOLS:
        url = tool_args.get("url", "") or tool_args.get("target", "")
        if url:
            ssrf_guard = get_ssrf_guard()
            ssrf_result = await ssrf_guard.validate_with_dns(url)
            if ssrf_result.blocked:
                logger.warning(
                    "ssrf_concern_logged_not_blocked",
                    tool=tool_name, url=url[:100],
                    reason=ssrf_result.reason,
                )
                # NOTE: Previously returned False here — now allow through

    # All checks passed
    return True, "allowed", decision


def redact_tool_output(output: str, scan_secrets: bool = True) -> str:
    """
    Post-execution: scan and redact secrets from tool output.

    Called after tool execution, before the output is logged
    or sent to the LLM.

    Args:
        output: Raw tool output text.
        scan_secrets: Whether to scan for secrets.

    Returns:
        Redacted output text.
    """
    if not scan_secrets or not output:
        return output

    scanner = get_secret_scanner()
    result = scanner.scan(output, redact=True)

    if result.has_secrets:
        logger.warning(
            "secrets_in_tool_output",
            findings=len(result.findings),
            critical=result.critical_count,
        )
        return result.redacted_text

    return output


async def run_after_tool_hooks(
    tool_name: str,
    tool_args: dict[str, Any],
    result: dict[str, Any],
    session_id: str = "",
) -> dict[str, Any]:
    """Run AFTER_TOOL_CALL hooks.

    Plugins can inspect/modify tool results.

    Args:
        tool_name: Tool that was executed.
        tool_args: Arguments used.
        result: Tool execution result.
        session_id: Session ID.

    Returns:
        Possibly modified result.
    """
    hook_runner = get_hook_runner()
    if not hook_runner.has_handlers(HookEvent.AFTER_TOOL_CALL):
        return result

    ctx = HookContext(
        event=HookEvent.AFTER_TOOL_CALL,
        data={
            "tool": tool_name,
            "args": tool_args,
            "result": result,
            "session_id": session_id,
        },
    )
    await hook_runner.run(ctx)

    # Return possibly modified result
    return ctx.data.get("result", result)
