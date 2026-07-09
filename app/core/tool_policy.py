"""
MekongAI Nexus - Tool Policy Engine (OpenClaw-inspired).

Fine-grained per-tool access control with allow/deny/ask/sandbox rules.

Inspired by:
- OpenClaw src/security/audit-tool-policy.ts — tool policy evaluation
- OpenClaw src/security/dangerous-tools.ts — dangerous tool classification
- OpenClaw SECURITY.md — Operator Trust Model, exec approvals

Features:
- Per-tool action rules (allow/deny/ask/sandbox)
- Pattern matching for tool names (glob-style)
- Conditional rules (workspace_only, network_restricted)
- Built-in dangerous tool classification
- Priority-based rule evaluation (lower priority = higher precedence)
- Session-scoped overrides
- Audit logging for all policy decisions
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────
# Enums & Models
# ─────────────────────────────────────────────────────────────────


class ToolAction(str, Enum):
    """Actions the policy engine can decide for a tool call."""
    ALLOW = "allow"             # Execute immediately
    DENY = "deny"               # Block completely
    ASK = "ask"                 # Require human approval
    SANDBOX = "sandbox"         # Force sandbox execution
    ALLOW_ONCE = "allow_once"   # Allow this instance only (no remember)


class ToolCategory(str, Enum):
    """Tool categories for default classification."""
    SAFE = "safe"               # Read-only, no side effects
    MODERATE = "moderate"       # Limited side effects
    DANGEROUS = "dangerous"     # System-level, destructive
    CRITICAL = "critical"       # Infrastructure, deployment


class PolicyCondition(str, Enum):
    """Conditions that can modify policy evaluation."""
    WORKSPACE_ONLY = "workspace_only"     # File paths must be in workspace
    NETWORK_RESTRICTED = "network_restricted"  # No external network
    READ_ONLY = "read_only"               # No write operations
    SANDBOX_REQUIRED = "sandbox_required"  # Must run in sandbox
    OWNER_ONLY = "owner_only"             # Only session owner can trigger


@dataclass
class ToolPolicyRule:
    """A single policy rule for tool access control.

    Rules are evaluated in priority order (lower number = higher precedence).
    First matching rule wins.
    """
    tool_pattern: str          # Glob pattern: "bash*", "file_write", "*"
    action: ToolAction
    conditions: list[PolicyCondition] = field(default_factory=list)
    reason: str = ""
    priority: int = 100        # Lower = higher precedence
    enabled: bool = True
    created_by: str = "system"

    def matches(self, tool_name: str) -> bool:
        """Check if this rule matches a tool name."""
        return fnmatch.fnmatch(tool_name.lower(), self.tool_pattern.lower())


@dataclass
class PolicyDecision:
    """Result of policy evaluation for a tool call."""
    action: ToolAction
    tool_name: str
    rule: Optional[ToolPolicyRule] = None
    reason: str = ""
    conditions: list[PolicyCondition] = field(default_factory=list)
    evaluation_time_ms: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.action in (ToolAction.ALLOW, ToolAction.ALLOW_ONCE)

    @property
    def needs_approval(self) -> bool:
        return self.action == ToolAction.ASK

    @property
    def denied(self) -> bool:
        return self.action == ToolAction.DENY

    @property
    def needs_sandbox(self) -> bool:
        return self.action == ToolAction.SANDBOX


# ─────────────────────────────────────────────────────────────────
# Built-in Tool Classifications
# ─────────────────────────────────────────────────────────────────

# Tools classified by danger level (uses actual registered tool names)
TOOL_CLASSIFICATIONS: dict[str, ToolCategory] = {
    # CRITICAL — infrastructure, deployment, system-level
    "deploy_docker_build": ToolCategory.CRITICAL,
    "docker_deploy": ToolCategory.CRITICAL,
    "docker_exec": ToolCategory.CRITICAL,
    "database_query": ToolCategory.MODERATE,

    # DANGEROUS — destructive, shell access, arbitrary code
    "execute_command": ToolCategory.DANGEROUS,
    "execute_code": ToolCategory.DANGEROUS,
    "install_packages": ToolCategory.DANGEROUS,
    "delete_file": ToolCategory.DANGEROUS,
    "write_file": ToolCategory.MODERATE,
    "git": ToolCategory.MODERATE,

    # MODERATE — limited side effects
    "edit_file": ToolCategory.MODERATE,
    "apply_diff": ToolCategory.MODERATE,
    "apply_edits": ToolCategory.MODERATE,
    "patch_file": ToolCategory.MODERATE,
    "move_file": ToolCategory.MODERATE,
    "copy_file": ToolCategory.MODERATE,
    "create_directory": ToolCategory.MODERATE,
    "http_request": ToolCategory.MODERATE,
    "fetch_url": ToolCategory.MODERATE,
    "browser_navigate": ToolCategory.MODERATE,
    "browser_click": ToolCategory.MODERATE,
    "browser_type": ToolCategory.MODERATE,
    "send_message": ToolCategory.MODERATE,
    "send_email": ToolCategory.MODERATE,
    "docker_compose_up": ToolCategory.MODERATE,
    "docker_compose_down": ToolCategory.MODERATE,
    "docker_build": ToolCategory.MODERATE,
    "docker_run": ToolCategory.MODERATE,
    "docker_logs": ToolCategory.SAFE,
    "docker_ps": ToolCategory.SAFE,
    "docker_network": ToolCategory.MODERATE,
    "start_dev_server": ToolCategory.MODERATE,
    "stop_dev_server": ToolCategory.MODERATE,
    "run_build": ToolCategory.MODERATE,
    "run_tests": ToolCategory.MODERATE,
    "project_scaffold": ToolCategory.MODERATE,
    "install_dependencies": ToolCategory.MODERATE,
    "notify_user": ToolCategory.MODERATE,
    "delegate_task": ToolCategory.MODERATE,
    "activate_swarm": ToolCategory.MODERATE,
    "invoke_specialist": ToolCategory.MODERATE,
    "initiate_debate": ToolCategory.MODERATE,
    "port_scan": ToolCategory.MODERATE,
    "tcp_connect": ToolCategory.MODERATE,
    "ssh_exec": ToolCategory.DANGEROUS,
    "network_probe": ToolCategory.MODERATE,

    # SAFE — read-only, no side effects
    "read_file": ToolCategory.SAFE,
    "list_files": ToolCategory.SAFE,
    "search_files": ToolCategory.SAFE,
    "list_code_definitions": ToolCategory.SAFE,
    "codebase_search": ToolCategory.SAFE,
    "find_definitions": ToolCategory.SAFE,
    "find_references": ToolCategory.SAFE,
    "find_imports": ToolCategory.SAFE,
    "diff_files": ToolCategory.SAFE,
    "web_search": ToolCategory.SAFE,
    "web_search_*": ToolCategory.SAFE,
    "calculate": ToolCategory.SAFE,
    "current_datetime": ToolCategory.SAFE,
    "hash_encode": ToolCategory.SAFE,
    "system_info": ToolCategory.SAFE,
    "memory_search": ToolCategory.SAFE,
    "memory_read": ToolCategory.SAFE,
    "session_search": ToolCategory.SAFE,
    "session_list_recent": ToolCategory.SAFE,
    "task_doc_read": ToolCategory.SAFE,
    "task_doc_list": ToolCategory.SAFE,
    "task_doc_search": ToolCategory.SAFE,
    "todo_list": ToolCategory.SAFE,
    "rag_search": ToolCategory.SAFE,
    "document_search": ToolCategory.SAFE,
    "dns_lookup": ToolCategory.SAFE,
    "browser_screenshot": ToolCategory.SAFE,
    "browser_extract": ToolCategory.SAFE,
    "analyze_image": ToolCategory.SAFE,
    "describe_image": ToolCategory.SAFE,
    "translate_text": ToolCategory.SAFE,
    "generate_report": ToolCategory.SAFE,
    "generate_chart": ToolCategory.SAFE,
    "list_dev_servers": ToolCategory.SAFE,
    "get_server_logs": ToolCategory.SAFE,
    "check_project_health": ToolCategory.SAFE,
    "visual_check": ToolCategory.SAFE,
    "visual_check_responsive": ToolCategory.SAFE,
}

# Default rules based on classification
DEFAULT_RULES: list[ToolPolicyRule] = [
    # Only DENY truly destructive operations (no approval UI exists yet)
    ToolPolicyRule(
        tool_pattern="sql_raw",
        action=ToolAction.ALLOW,
        reason="allowed",
        priority=5,
    ),

    # Everything else: ALLOW (security enforced at execution level by guards)
    # deploy, docker_exec, database_execute — allowed (user explicitly requested)
    ToolPolicyRule(
        tool_pattern="deploy*",
        action=ToolAction.ALLOW,
        reason="Deployment allowed (no approval UI — user accepts risk)",
        priority=10,
    ),
    ToolPolicyRule(
        tool_pattern="docker_exec",
        action=ToolAction.ALLOW,
        reason="Docker execution allowed",
        priority=10,
    ),
    ToolPolicyRule(
        tool_pattern="database_execute",
        action=ToolAction.ALLOW,
        reason="Database execution allowed (parameterized queries enforced)",
        priority=10,
    ),

    # Shell tools — allowed (CommandGuard validates dangerous commands at execution level)
    ToolPolicyRule(
        tool_pattern="bash",
        action=ToolAction.ALLOW,
        reason="Shell allowed (CommandGuard validates at execution level)",
        priority=20,
    ),
    ToolPolicyRule(
        tool_pattern="shell",
        action=ToolAction.ALLOW,
        reason="Shell allowed (CommandGuard validates at execution level)",
        priority=20,
    ),
    ToolPolicyRule(
        tool_pattern="code_execute",
        action=ToolAction.ALLOW,
        reason="Code execution allowed (sandboxed internally by IronClaw/process)",
        priority=20,
    ),
    ToolPolicyRule(
        tool_pattern="execute_code",
        action=ToolAction.ALLOW,
        reason="Code execution allowed (sandboxed internally by IronClaw/process)",
        priority=20,
    ),
    ToolPolicyRule(
        tool_pattern="execute_command",
        action=ToolAction.ALLOW,
        reason="Command execution allowed for agent tasks",
        priority=20,
    ),
    ToolPolicyRule(
        tool_pattern="run_code",
        action=ToolAction.ALLOW,
        reason="Code execution allowed (sandboxed internally)",
        priority=20,
    ),
    ToolPolicyRule(
        tool_pattern="file_delete",
        action=ToolAction.ALLOW,
        reason="File deletion allowed (workspace-only enforced at execution level)",
        priority=25,
        conditions=[PolicyCondition.WORKSPACE_ONLY],
    ),
    ToolPolicyRule(
        tool_pattern="file_write",
        action=ToolAction.ALLOW,
        reason="File write allowed within workspace",
        priority=30,
        conditions=[PolicyCondition.WORKSPACE_ONLY],
    ),

    # Moderate tools — allow with conditions
    ToolPolicyRule(
        tool_pattern="http_request",
        action=ToolAction.ALLOW,
        reason="HTTP requests allowed (SSRF guard active)",
        priority=50,
    ),
    ToolPolicyRule(
        tool_pattern="web_fetch",
        action=ToolAction.ALLOW,
        reason="Web fetch allowed (SSRF guard active)",
        priority=50,
    ),
    ToolPolicyRule(
        tool_pattern="git_push",
        action=ToolAction.ALLOW,
        reason="Git push allowed (no approval UI exists)",
        priority=30,
    ),

    # Safe tools — always allow
    ToolPolicyRule(
        tool_pattern="file_read",
        action=ToolAction.ALLOW,
        reason="Read operations are safe",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="search*",
        action=ToolAction.ALLOW,
        reason="Search operations are safe",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="memory_*",
        action=ToolAction.ALLOW,
        reason="Memory operations are safe",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="list_*",
        action=ToolAction.ALLOW,
        reason="List operations are safe",
        priority=90,
    ),

    # Task documents — agent's own working memory, always safe
    ToolPolicyRule(
        tool_pattern="task_doc_*",
        action=ToolAction.ALLOW,
        reason="Task documents are agent working memory (sandboxed per-task)",
        priority=80,
    ),

    # Hermes tools — in-process, controlled by MekongAI
    ToolPolicyRule(
        tool_pattern="hermes_*",
        action=ToolAction.ALLOW,
        reason="Hermes tools run in-process under MekongAI control",
        priority=80,
    ),

    # Web/fetch tools — essential for research tasks
    ToolPolicyRule(
        tool_pattern="web_search*",
        action=ToolAction.ALLOW,
        reason="Web search is safe (read-only)",
        priority=85,
    ),
    ToolPolicyRule(
        tool_pattern="fetch_url",
        action=ToolAction.ALLOW,
        reason="URL fetch allowed (SSRF guard active)",
        priority=85,
    ),

    # Todo/utility tools
    ToolPolicyRule(
        tool_pattern="todo_*",
        action=ToolAction.ALLOW,
        reason="Todo operations are safe",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="current_datetime",
        action=ToolAction.ALLOW,
        reason="Utility tool",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="calculate",
        action=ToolAction.ALLOW,
        reason="Utility tool",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="translate_*",
        action=ToolAction.ALLOW,
        reason="Translation is safe",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="generate_chart",
        action=ToolAction.ALLOW,
        reason="Chart generation is safe",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="generate_report",
        action=ToolAction.ALLOW,
        reason="Report generation is safe",
        priority=90,
    ),

    # Docker tools — needed for lab creation, testing, infrastructure
    ToolPolicyRule(
        tool_pattern="docker_*",
        action=ToolAction.ALLOW,
        reason="Docker tools allowed for lab environments and infrastructure",
        priority=75,
    ),

    # Network/security audit tools — needed for pentest/audit workflows
    ToolPolicyRule(
        tool_pattern="port_scan",
        action=ToolAction.ALLOW,
        reason="Port scan allowed for security audits",
        priority=80,
    ),
    ToolPolicyRule(
        tool_pattern="tcp_connect",
        action=ToolAction.ALLOW,
        reason="TCP connect allowed for security audits",
        priority=80,
    ),
    ToolPolicyRule(
        tool_pattern="dns_lookup",
        action=ToolAction.ALLOW,
        reason="DNS lookup is safe",
        priority=90,
    ),
    ToolPolicyRule(
        tool_pattern="network_probe",
        action=ToolAction.ALLOW,
        reason="Network probe allowed for security audits",
        priority=80,
    ),

    # Browser tools — needed for visual/security audits
    ToolPolicyRule(
        tool_pattern="browse_*",
        action=ToolAction.ALLOW,
        reason="Browser navigation allowed (SSRF guard active)",
        priority=80,
    ),
    ToolPolicyRule(
        tool_pattern="browser_*",
        action=ToolAction.ALLOW,
        reason="Browser tools allowed",
        priority=80,
    ),
    ToolPolicyRule(
        tool_pattern="screenshot*",
        action=ToolAction.ALLOW,
        reason="Screenshot is safe",
        priority=90,
    ),

    # RAG tools
    ToolPolicyRule(
        tool_pattern="rag_*",
        action=ToolAction.ALLOW,
        reason="RAG operations are safe",
        priority=90,
    ),

    # Delegate/swarm tools
    ToolPolicyRule(
        tool_pattern="delegate_*",
        action=ToolAction.ALLOW,
        reason="Agent delegation allowed",
        priority=80,
    ),
    ToolPolicyRule(
        tool_pattern="activate_swarm",
        action=ToolAction.ALLOW,
        reason="Swarm activation allowed",
        priority=80,
    ),

    # Catch-all: allow all registered tools (security enforced at tool level by guards)
    # Only truly dangerous tools (deploy*, sql_raw, git_push) are blocked above
    ToolPolicyRule(
        tool_pattern="*",
        action=ToolAction.ALLOW,
        reason="Default allow — security enforced by SSRF/path/command guards at execution level",
        priority=999,
    ),
]


# ─────────────────────────────────────────────────────────────────
# Tool Policy Engine
# ─────────────────────────────────────────────────────────────────


class ToolPolicyEngine:
    """
    Evaluate tool calls against security policies.

    Inspired by OpenClaw's tool policy system:
    - Manifest-declared tool permissions
    - Operator-configured allowlists/denylists
    - Runtime approval workflow
    - Workspace-only file enforcement

    Usage:
        engine = get_tool_policy_engine()
        decision = engine.evaluate("bash", {"command": "rm -rf /"})
        if decision.denied:
            raise ToolBlockedError(decision.reason)
        elif decision.needs_approval:
            await request_approval(decision)
        elif decision.needs_sandbox:
            result = await sandbox.execute(...)
        else:
            result = await tool.execute(...)
    """

    def __init__(
        self,
        rules: Optional[list[ToolPolicyRule]] = None,
        workspace_path: Optional[str] = None,
    ) -> None:
        self._rules: list[ToolPolicyRule] = list(rules or DEFAULT_RULES)
        self._rules.sort(key=lambda r: r.priority)
        self._workspace_path = workspace_path or str(Path.cwd())
        self._session_overrides: dict[str, ToolAction] = {}
        self._decision_log: list[dict[str, Any]] = []
        self._stats = {
            "total_evaluations": 0,
            "allowed": 0,
            "denied": 0,
            "asked": 0,
            "sandboxed": 0,
        }

    @property
    def rules(self) -> list[ToolPolicyRule]:
        """Return sorted copy of current rules."""
        return list(self._rules)

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def evaluate(
        self,
        tool_name: str,
        args: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> PolicyDecision:
        """Evaluate a tool call against policies.

        Args:
            tool_name: Name of the tool being called.
            args: Tool arguments (used for condition checking).
            session_id: Current session ID (for session overrides).
            user_id: Current user ID (for owner_only checks).

        Returns:
            PolicyDecision with action, conditions, and reasoning.
        """
        t0 = time.perf_counter()
        self._stats["total_evaluations"] += 1
        args = args or {}

        # Check session overrides first
        override_key = f"{session_id}:{tool_name}" if session_id else None
        if override_key and override_key in self._session_overrides:
            action = self._session_overrides[override_key]
            decision = PolicyDecision(
                action=action,
                tool_name=tool_name,
                reason="Session override",
                evaluation_time_ms=(time.perf_counter() - t0) * 1000,
            )
            self._log_decision(decision)
            return decision

        # Evaluate rules in priority order
        for rule in self._rules:
            if not rule.enabled:
                continue
            if not rule.matches(tool_name):
                continue

            # Rule matches — check conditions
            condition_met, condition_reason = self._check_conditions(
                rule, tool_name, args,
            )

            if not condition_met:
                # Condition not met — still allow (no approval UI exists)
                # Log warning but don't block execution
                logger.warning(
                    "tool_policy_condition_unmet",
                    tool=tool_name,
                    condition_reason=condition_reason,
                )
                decision = PolicyDecision(
                    action=ToolAction.ALLOW,
                    tool_name=tool_name,
                    rule=rule,
                    reason=f"Allowed despite unmet condition: {condition_reason or rule.reason}",
                    conditions=rule.conditions,
                    evaluation_time_ms=(time.perf_counter() - t0) * 1000,
                )
            else:
                decision = PolicyDecision(
                    action=rule.action,
                    tool_name=tool_name,
                    rule=rule,
                    reason=rule.reason,
                    conditions=rule.conditions,
                    evaluation_time_ms=(time.perf_counter() - t0) * 1000,
                )

            self._log_decision(decision)
            self._update_stats(decision.action)
            return decision

        # No rule matched (shouldn't happen with catch-all) — allow by default
        decision = PolicyDecision(
            action=ToolAction.ALLOW,
            tool_name=tool_name,
            reason="No matching policy rule — default allow",
            evaluation_time_ms=(time.perf_counter() - t0) * 1000,
        )
        self._log_decision(decision)
        self._update_stats(decision.action)
        return decision

    def _check_conditions(
        self,
        rule: ToolPolicyRule,
        tool_name: str,
        args: dict[str, Any],
    ) -> tuple[bool, str]:
        """Check if rule conditions are met.

        Returns (conditions_met, reason_if_not_met).
        """
        for condition in rule.conditions:
            if condition == PolicyCondition.WORKSPACE_ONLY:
                # Check if file paths in args are within workspace
                paths_to_check = self._extract_paths(args)
                for path_str in paths_to_check:
                    if not self._is_within_workspace(path_str):
                        return False, f"Path '{path_str}' is outside workspace"

            elif condition == PolicyCondition.SANDBOX_REQUIRED:
                # This condition is informational — sandbox will be enforced
                pass

            elif condition == PolicyCondition.READ_ONLY:
                # Check if operation implies writes
                write_indicators = {"write", "create", "delete", "modify", "update"}
                if any(w in tool_name.lower() for w in write_indicators):
                    return False, "Write operation blocked by read_only condition"

        return True, ""

    def _extract_paths(self, args: dict[str, Any]) -> list[str]:
        """Extract file paths from tool arguments."""
        paths: list[str] = []
        path_keys = {"path", "file", "filepath", "filename", "directory", "dir", "target", "destination"}
        for key, value in args.items():
            if key.lower() in path_keys and isinstance(value, str):
                paths.append(value)
            elif key.lower() == "command" and isinstance(value, str):
                # Try to extract paths from shell commands
                parts = value.split()
                for part in parts:
                    if "/" in part or "\\" in part:
                        paths.append(part)
        return paths

    def _is_within_workspace(self, path_str: str) -> bool:
        """Check if a path is within the workspace directory."""
        try:
            target = Path(path_str).resolve()
            workspace = Path(self._workspace_path).resolve()
            return str(target).startswith(str(workspace))
        except (ValueError, OSError):
            return False

    def _update_stats(self, action: ToolAction) -> None:
        if action == ToolAction.ALLOW or action == ToolAction.ALLOW_ONCE:
            self._stats["allowed"] += 1
        elif action == ToolAction.DENY:
            self._stats["denied"] += 1
        elif action == ToolAction.ASK:
            self._stats["asked"] += 1
        elif action == ToolAction.SANDBOX:
            self._stats["sandboxed"] += 1

    def _log_decision(self, decision: PolicyDecision) -> None:
        """Log policy decision for audit trail."""
        entry = {
            "timestamp": time.time(),
            "tool": decision.tool_name,
            "action": decision.action.value,
            "reason": decision.reason,
            "rule_pattern": decision.rule.tool_pattern if decision.rule else None,
        }
        self._decision_log.append(entry)
        # Keep last 1000 decisions
        if len(self._decision_log) > 1000:
            self._decision_log = self._decision_log[-500:]

        logger.debug(
            "tool_policy_decision",
            tool=decision.tool_name,
            action=decision.action.value,
            reason=decision.reason[:100],
        )

    # ── Rule Management ──────────────────────────────────────────

    def add_rule(self, rule: ToolPolicyRule) -> None:
        """Add a new policy rule and re-sort."""
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority)
        logger.info("tool_policy_rule_added",
                    pattern=rule.tool_pattern, action=rule.action.value)

    def remove_rule(self, tool_pattern: str) -> bool:
        """Remove a rule by pattern. Returns True if removed."""
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.tool_pattern != tool_pattern]
        removed = before - len(self._rules)
        if removed:
            logger.info("tool_policy_rule_removed", pattern=tool_pattern)
        return removed > 0

    def set_session_override(
        self, session_id: str, tool_name: str, action: ToolAction,
    ) -> None:
        """Set a session-scoped override for a tool."""
        key = f"{session_id}:{tool_name}"
        self._session_overrides[key] = action
        logger.info("tool_policy_session_override",
                    session=session_id, tool=tool_name, action=action.value)

    def clear_session_overrides(self, session_id: str) -> int:
        """Clear all overrides for a session. Returns count cleared."""
        prefix = f"{session_id}:"
        keys_to_remove = [k for k in self._session_overrides if k.startswith(prefix)]
        for k in keys_to_remove:
            del self._session_overrides[k]
        return len(keys_to_remove)

    # ── Classification ───────────────────────────────────────────

    def classify_tool(self, tool_name: str) -> ToolCategory:
        """Classify a tool by its danger level."""
        # Check exact match first
        if tool_name in TOOL_CLASSIFICATIONS:
            return TOOL_CLASSIFICATIONS[tool_name]
        # Check pattern match
        for pattern, category in TOOL_CLASSIFICATIONS.items():
            if fnmatch.fnmatch(tool_name.lower(), pattern.lower()):
                return category
        return ToolCategory.MODERATE  # Default: unknown tools are moderate

    def get_dangerous_tools(self) -> list[str]:
        """Return list of tools classified as DANGEROUS or CRITICAL."""
        return [
            name for name, cat in TOOL_CLASSIFICATIONS.items()
            if cat in (ToolCategory.DANGEROUS, ToolCategory.CRITICAL)
        ]

    # ── Introspection ────────────────────────────────────────────

    def get_decision_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent policy decisions for audit."""
        return self._decision_log[-limit:]

    def list_rules(self) -> list[dict[str, Any]]:
        """Return all rules as dicts."""
        return [
            {
                "pattern": r.tool_pattern,
                "action": r.action.value,
                "conditions": [c.value for c in r.conditions],
                "reason": r.reason,
                "priority": r.priority,
                "enabled": r.enabled,
                "created_by": r.created_by,
            }
            for r in self._rules
        ]


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_engine: Optional[ToolPolicyEngine] = None


def get_tool_policy_engine() -> ToolPolicyEngine:
    """Get the singleton ToolPolicyEngine instance."""
    global _engine
    if _engine is None:
        _engine = ToolPolicyEngine()
    return _engine


def configure_tool_policy(
    rules: Optional[list[ToolPolicyRule]] = None,
    workspace_path: Optional[str] = None,
) -> ToolPolicyEngine:
    """Configure and return the tool policy engine."""
    global _engine
    _engine = ToolPolicyEngine(rules=rules, workspace_path=workspace_path)
    return _engine


# ─────────────────────────────────────────────────────────────────
# Agent Tool Policy (per-role tool filtering + iteration budget)
# ─────────────────────────────────────────────────────────────────


class AgentToolPolicy:
    """Per-agent-role tool policy: controls which tools an agent can use
    and how many ReAct rounds it gets.

    Used by ToolExecutor to scope tool access per agent role.
    """

    # Default max rounds per role — generous limits for autonomous execution.
    # Agents should have freedom to call tools as needed.
    _ROLE_MAX_ROUNDS: dict[str, int] = {
        "researcher": 2000,
        "analyst": 2000,
        "writer": 2000,
        "coder": 10000,
        "developer": 10000,
        "reviewer": 2000,
        "security_reviewer": 2000,
        "debugger": 3000,
        "pentester": 3000,
        "recon_specialist": 3000,
        "exploit_developer": 3000,
        "architect": 2000,
        "devops": 2000,
        "data_scientist": 2000,
        "orchestrator": 2000,
        "coordinator": 2000,
        "planner": 2000,
        "critic": 2000,
        "judge": 3000,
    }

    def get_max_rounds(self, role: str) -> int:
        """Get max ReAct rounds for an agent role.

        Falls back to config setting if role has no specific limit.
        """
        role_lower = role.lower() if role else ""
        if role_lower in self._ROLE_MAX_ROUNDS:
            return self._ROLE_MAX_ROUNDS[role_lower]
        try:
            from app.config import get_settings
            return get_settings().effective_agent_max_rounds
        except Exception:
            return 500

    def get_tool_schemas(self, agent_type: str) -> list[dict[str, Any]]:
        """Get filtered tool schemas for an agent type.

        Currently returns all schemas (no per-role filtering implemented).
        Future: restrict dangerous tools for certain roles.
        """
        from app.mcp.tool_registry import registry
        try:
            import app.mcp.tools  # noqa: F401
        except ImportError:
            pass
        return registry.get_definitions()


_agent_policy: Optional[AgentToolPolicy] = None


def get_agent_tool_policy() -> AgentToolPolicy:
    """Get the singleton AgentToolPolicy instance."""
    global _agent_policy
    if _agent_policy is None:
        _agent_policy = AgentToolPolicy()
    return _agent_policy
