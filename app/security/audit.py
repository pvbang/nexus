"""
MekongAI Nexus - Security Audit Framework (OpenClaw-inspired).

Comprehensive security scanning with multi-module architecture.

Inspired by:
- OpenClaw src/security/ — 60+ audit modules
- OpenClaw CLI: `openclaw security audit [--deep]`
- OpenClaw src/security/audit.ts — main audit runner

Features:
- Modular audit checks (config, plugins, tools, network, secrets)
- Severity-graded findings (critical/high/medium/low/info)
- Auto-fix for fixable issues
- Periodic scheduling via cron
- JSON/human-readable output
- Deep mode for thorough scanning
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import structlog

from app.config import get_settings
from app.core.tool_policy import get_tool_policy_engine, TOOL_CLASSIFICATIONS, ToolCategory
from app.plugins.registry import get_plugin_registry
from app.security.ssrf_guard import get_ssrf_guard

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────
# Enums & Models
# ─────────────────────────────────────────────────────────────────


class AuditSeverity(str, Enum):
    """Severity levels for audit findings."""
    CRITICAL = "critical"   # Immediate action required
    HIGH = "high"           # Should fix soon
    MEDIUM = "medium"       # Recommended fix
    LOW = "low"             # Nice to have
    INFO = "info"           # Informational


class AuditCategory(str, Enum):
    """Categories of audit checks."""
    CONFIG = "config"
    PLUGIN = "plugin"
    TOOL = "tool"
    NETWORK = "network"
    SECRET = "secret"
    SANDBOX = "sandbox"
    AUTH = "auth"
    MEMORY = "memory"


@dataclass
class AuditFinding:
    """A single finding from a security audit."""
    id: str
    title: str
    severity: AuditSeverity
    category: AuditCategory
    description: str
    remediation: str
    affected_component: str = ""
    auto_fixable: bool = False
    fixed: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "severity": self.severity.value,
            "category": self.category.value,
            "description": self.description,
            "remediation": self.remediation,
            "affected_component": self.affected_component,
            "auto_fixable": self.auto_fixable,
            "fixed": self.fixed,
        }


@dataclass
class AuditReport:
    """Complete audit report."""
    findings: list[AuditFinding] = field(default_factory=list)
    run_at: float = field(default_factory=time.time)
    duration_ms: float = 0.0
    deep_mode: bool = False
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AuditSeverity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AuditSeverity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == AuditSeverity.MEDIUM)

    @property
    def total_findings(self) -> int:
        return len(self.findings)

    @property
    def severity_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sev in AuditSeverity:
            counts[sev.value] = sum(1 for f in self.findings if f.severity == sev)
        return counts

    @property
    def passed(self) -> bool:
        return self.critical_count == 0 and self.high_count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_at": self.run_at,
            "duration_ms": round(self.duration_ms, 1),
            "deep_mode": self.deep_mode,
            "checks_run": self.checks_run,
            "checks_passed": self.checks_passed,
            "total_findings": self.total_findings,
            "severity_counts": self.severity_counts,
            "passed": self.passed,
            "findings": [f.to_dict() for f in self.findings],
        }

    def format_summary(self) -> str:
        """Human-readable summary."""
        status = "✅ PASSED" if self.passed else "❌ FAILED"
        lines = [
            f"Security Audit {status}",
            f"{'─' * 50}",
            f"Checks run: {self.checks_run} | Passed: {self.checks_passed}",
            f"Duration: {self.duration_ms:.0f}ms | Deep mode: {self.deep_mode}",
            f"",
            f"Findings: {self.total_findings}",
        ]
        for sev in AuditSeverity:
            count = self.severity_counts.get(sev.value, 0)
            if count > 0:
                emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}.get(sev.value, "")
                lines.append(f"  {emoji} {sev.value.upper()}: {count}")

        if self.findings:
            lines.append("")
            lines.append("Top findings:")
            for f in sorted(self.findings, key=lambda x: list(AuditSeverity).index(x.severity))[:10]:
                lines.append(f"  [{f.severity.value.upper()}] {f.title}")
                lines.append(f"    → {f.remediation}")

        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# Audit Runner
# ─────────────────────────────────────────────────────────────────


class SecurityAuditRunner:
    """
    Main security audit runner.

    Runs multiple audit modules and aggregates findings.

    Usage:
        runner = SecurityAuditRunner()
        report = await runner.run_audit(deep=False)
        print(report.format_summary())
    """

    def __init__(self) -> None:
        self._last_report: Optional[AuditReport] = None

    @property
    def last_report(self) -> Optional[AuditReport]:
        return self._last_report

    async def run_audit(self, deep: bool = False) -> AuditReport:
        """Run full security audit.

        Args:
            deep: If True, run expensive/slow checks (plugin code analysis, etc.)

        Returns:
            AuditReport with all findings.
        """
        t0 = time.perf_counter()
        report = AuditReport(deep_mode=deep)

        # Run all audit modules
        audit_modules = [
            ("config", self._audit_config),
            ("auth", self._audit_auth),
            ("tool_policy", self._audit_tool_policy),
            ("plugins", self._audit_plugins),
            ("sandbox", self._audit_sandbox),
            ("network", self._audit_network),
        ]

        if deep:
            audit_modules.extend([
                ("secrets", self._audit_secrets),
                ("memory", self._audit_memory),
            ])

        for module_name, audit_fn in audit_modules:
            try:
                findings = await audit_fn()
                report.findings.extend(findings)
                report.checks_run += 1
                if not any(f.severity in (AuditSeverity.CRITICAL, AuditSeverity.HIGH) for f in findings):
                    report.checks_passed += 1
            except Exception as exc:
                logger.warning("audit_module_error", module=module_name, error=str(exc))
                report.findings.append(AuditFinding(
                    id=f"audit_error_{module_name}",
                    title=f"Audit module '{module_name}' failed",
                    severity=AuditSeverity.INFO,
                    category=AuditCategory.CONFIG,
                    description=f"Module raised: {exc}",
                    remediation="Check module dependencies and configuration",
                ))
                report.checks_run += 1

        report.duration_ms = (time.perf_counter() - t0) * 1000
        self._last_report = report

        logger.info(
            "security_audit_complete",
            total_findings=report.total_findings,
            critical=report.critical_count,
            high=report.high_count,
            passed=report.passed,
            duration_ms=round(report.duration_ms),
        )

        return report

    async def run_fix(self, report: Optional[AuditReport] = None) -> dict[str, Any]:
        """Auto-fix fixable findings.

        Args:
            report: Report to fix. Uses last_report if None.

        Returns:
            Dict with {fixed: int, failed: int, details: [...]}
        """
        report = report or self._last_report
        if report is None:
            return {"fixed": 0, "failed": 0, "details": []}

        fixed = 0
        failed = 0
        details: list[dict[str, str]] = []

        for finding in report.findings:
            if not finding.auto_fixable or finding.fixed:
                continue
            try:
                success = await self._apply_fix(finding)
                if success:
                    finding.fixed = True
                    fixed += 1
                    details.append({"id": finding.id, "status": "fixed"})
                else:
                    failed += 1
                    details.append({"id": finding.id, "status": "failed"})
            except Exception as exc:
                failed += 1
                details.append({"id": finding.id, "status": f"error: {exc}"})

        return {"fixed": fixed, "failed": failed, "details": details}

    async def _apply_fix(self, finding: AuditFinding) -> bool:
        """Apply auto-fix for a finding. Returns True if successful."""
        # Implement specific fixes based on finding ID
        if finding.id == "config_debug_production":
            # Can't auto-fix env var but can log recommendation
            logger.info("fix_recommendation", finding=finding.id,
                        action="Set APP_DEBUG=false in .env")
            return False  # Env vars can't be auto-fixed
        return False

    # ── Audit Modules ────────────────────────────────────────────

    async def _audit_config(self) -> list[AuditFinding]:
        """Audit application configuration."""
        findings: list[AuditFinding] = []
        settings = get_settings()

        # Check default secrets
        if settings.app_secret_key == "change-me-in-production":
            findings.append(AuditFinding(
                id="config_default_secret",
                title="Application secret key is default value",
                severity=AuditSeverity.CRITICAL,
                category=AuditCategory.CONFIG,
                description="APP_SECRET_KEY is still the default placeholder value",
                remediation="Set APP_SECRET_KEY to a strong random value in .env",
                affected_component="app.config.Settings.app_secret_key",
                auto_fixable=False,
            ))

        if settings.jwt_secret_key == "change-me-jwt-secret":
            findings.append(AuditFinding(
                id="config_default_jwt_secret",
                title="JWT secret key is default value",
                severity=AuditSeverity.CRITICAL,
                category=AuditCategory.CONFIG,
                description="JWT_SECRET_KEY is the default — tokens are predictable",
                remediation="Set JWT_SECRET_KEY to a cryptographically random value",
                affected_component="app.config.Settings.jwt_secret_key",
                auto_fixable=False,
            ))

        # Debug mode in production
        if settings.app_env == "production" and settings.app_debug:
            findings.append(AuditFinding(
                id="config_debug_production",
                title="Debug mode enabled in production",
                severity=AuditSeverity.HIGH,
                category=AuditCategory.CONFIG,
                description="APP_DEBUG=true in production exposes stack traces and debug info",
                remediation="Set APP_DEBUG=false for production deployments",
                affected_component="app.config.Settings.app_debug",
                auto_fixable=True,
            ))

        # Database URL with credentials
        if settings.database_url and "@" in settings.database_url:
            # Check if using default credentials
            if "nexus_password" in settings.database_url:
                findings.append(AuditFinding(
                    id="config_default_db_password",
                    title="Default database password in use",
                    severity=AuditSeverity.HIGH,
                    category=AuditCategory.CONFIG,
                    description="Database is using the default password 'nexus_password'",
                    remediation="Change DATABASE_URL to use a strong, unique password",
                    affected_component="app.config.Settings.database_url",
                ))

        # Check host binding
        if settings.app_env == "production" and settings.app_host == "0.0.0.0":
            findings.append(AuditFinding(
                id="config_bind_all_interfaces",
                title="Application bound to all interfaces",
                severity=AuditSeverity.MEDIUM,
                category=AuditCategory.CONFIG,
                description="APP_HOST=0.0.0.0 exposes service to all network interfaces",
                remediation="Bind to 127.0.0.1 and use a reverse proxy, or restrict via firewall",
                affected_component="app.config.Settings.app_host",
            ))

        return findings

    async def _audit_auth(self) -> list[AuditFinding]:
        """Audit authentication configuration."""
        findings: list[AuditFinding] = []
        settings = get_settings()

        # Check if auth is effectively disabled
        from app.core.auth import AUTH_ENABLED
        if not AUTH_ENABLED and settings.app_env == "production":
            findings.append(AuditFinding(
                id="auth_disabled_production",
                title="Authentication disabled in production",
                severity=AuditSeverity.CRITICAL,
                category=AuditCategory.AUTH,
                description="JWT auth is disabled (default secret key) but app_env=production",
                remediation="Set JWT_SECRET_KEY to enable authentication",
                affected_component="app.core.auth.AUTH_ENABLED",
            ))

        # Check JWT expiry
        if hasattr(settings, "jwt_expire_minutes"):
            if settings.jwt_expire_minutes > 1440:  # 24 hours
                findings.append(AuditFinding(
                    id="auth_long_token_expiry",
                    title="JWT token expiry too long",
                    severity=AuditSeverity.MEDIUM,
                    category=AuditCategory.AUTH,
                    description=f"JWT expires in {settings.jwt_expire_minutes} minutes (>24h)",
                    remediation="Reduce JWT_EXPIRE_MINUTES to 60-480 (1-8 hours)",
                    affected_component="app.config.Settings.jwt_expire_minutes",
                ))

        return findings

    async def _audit_tool_policy(self) -> list[AuditFinding]:
        """Audit tool policy configuration."""
        findings: list[AuditFinding] = []
        engine = get_tool_policy_engine()

        # Check if dangerous tools have policies
        dangerous_tools = [
            name for name, cat in TOOL_CLASSIFICATIONS.items()
            if cat in (ToolCategory.DANGEROUS, ToolCategory.CRITICAL)
        ]

        rules = engine.list_rules()
        covered_patterns = {r["pattern"] for r in rules if r["enabled"]}

        for tool in dangerous_tools:
            # Check if any rule covers this tool
            is_covered = any(
                engine._rules[i].matches(tool)
                for i in range(len(engine._rules))
                if engine._rules[i].enabled
            )
            if not is_covered:
                findings.append(AuditFinding(
                    id=f"tool_no_policy_{tool}",
                    title=f"Dangerous tool '{tool}' has no policy rule",
                    severity=AuditSeverity.HIGH,
                    category=AuditCategory.TOOL,
                    description=f"Tool '{tool}' is classified as dangerous but has no policy",
                    remediation=f"Add a policy rule for '{tool}' (recommended: ask or deny)",
                    affected_component="app.core.tool_policy",
                ))

        # Check for overly permissive catch-all
        catchall_rules = [r for r in rules if r["pattern"] == "*" and r["action"] == "allow"]
        if catchall_rules:
            findings.append(AuditFinding(
                id="tool_permissive_catchall",
                title="Catch-all rule allows all tools",
                severity=AuditSeverity.HIGH,
                category=AuditCategory.TOOL,
                description="A '*' pattern with action=allow bypasses all tool security",
                remediation="Change catch-all to action=ask for unknown tools",
                affected_component="app.core.tool_policy",
            ))

        return findings

    async def _audit_plugins(self) -> list[AuditFinding]:
        """Audit plugin trust and configuration."""
        findings: list[AuditFinding] = []
        registry = get_plugin_registry()
        plugins = registry.list_all()

        for plugin_info in plugins:
            plugin_id = plugin_info.get("id", "unknown")
            capabilities = plugin_info.get("capabilities", [])

            # Check tool_provider plugins without approval requirement
            if "tool_provider" in capabilities:
                loaded = registry.get(plugin_id)
                if loaded and not loaded.manifest.requires_approval:
                    findings.append(AuditFinding(
                        id=f"plugin_tool_no_approval_{plugin_id}",
                        title=f"Plugin '{plugin_id}' provides tools without approval flag",
                        severity=AuditSeverity.MEDIUM,
                        category=AuditCategory.PLUGIN,
                        description=f"Plugin '{plugin_id}' has tool_provider capability but requires_approval=false",
                        remediation=f"Set requires_approval=true in plugin.json for '{plugin_id}'",
                        affected_component=f"plugins/{plugin_id}/plugin.json",
                    ))

            # Check dangerous flag
            if plugin_info.get("dangerous"):
                findings.append(AuditFinding(
                    id=f"plugin_dangerous_{plugin_id}",
                    title=f"Plugin '{plugin_id}' is marked as dangerous",
                    severity=AuditSeverity.MEDIUM,
                    category=AuditCategory.PLUGIN,
                    description=f"Plugin '{plugin_id}' self-declares as dangerous — review carefully",
                    remediation="Review plugin code and ensure it's from a trusted source",
                    affected_component=f"plugins/{plugin_id}",
                ))

        return findings

    async def _audit_sandbox(self) -> list[AuditFinding]:
        """Audit sandbox configuration."""
        findings: list[AuditFinding] = []
        settings = get_settings()

        if not settings.docker_sandbox_enabled:
            findings.append(AuditFinding(
                id="sandbox_disabled",
                title="Docker sandbox is disabled",
                severity=AuditSeverity.MEDIUM,
                category=AuditCategory.SANDBOX,
                description="Code execution runs without container isolation",
                remediation="Set DOCKER_SANDBOX_ENABLED=true for production",
                affected_component="app.config.Settings.docker_sandbox_enabled",
            ))

        # Check sandbox memory limits
        if settings.docker_sandbox_enabled:
            memory_str = settings.docker_sandbox_memory
            # Parse memory value
            try:
                if memory_str.endswith("g"):
                    memory_mb = int(memory_str[:-1]) * 1024
                elif memory_str.endswith("m"):
                    memory_mb = int(memory_str[:-1])
                else:
                    memory_mb = int(memory_str)

                if memory_mb > 1024:
                    findings.append(AuditFinding(
                        id="sandbox_high_memory",
                        title="Sandbox memory limit is high",
                        severity=AuditSeverity.LOW,
                        category=AuditCategory.SANDBOX,
                        description=f"Sandbox memory limit ({memory_str}) may be too generous",
                        remediation="Consider reducing to 256m-512m for most use cases",
                        affected_component="app.config.Settings.docker_sandbox_memory",
                    ))
            except (ValueError, AttributeError):
                pass

        return findings

    async def _audit_network(self) -> list[AuditFinding]:
        """Audit network security (SSRF guard status)."""
        findings: list[AuditFinding] = []
        guard = get_ssrf_guard()

        # Check if SSRF guard is configured
        if guard._config.allow_private:
            findings.append(AuditFinding(
                id="network_ssrf_allow_private",
                title="SSRF guard allows private networks",
                severity=AuditSeverity.HIGH,
                category=AuditCategory.NETWORK,
                description="SSRF guard configured with allow_private=True",
                remediation="Set allow_private=False unless internal service access is required",
                affected_component="app.security.ssrf_guard.SSRFConfig",
            ))

        if guard._config.allow_localhost:
            findings.append(AuditFinding(
                id="network_ssrf_allow_localhost",
                title="SSRF guard allows localhost",
                severity=AuditSeverity.MEDIUM,
                category=AuditCategory.NETWORK,
                description="SSRF guard configured with allow_localhost=True",
                remediation="Disable localhost access unless needed for development",
                affected_component="app.security.ssrf_guard.SSRFConfig",
            ))

        return findings

    async def _audit_secrets(self) -> list[AuditFinding]:
        """Deep mode: Scan configuration and logs for exposed secrets."""
        findings: list[AuditFinding] = []

        # Scan .env.example for accidentally committed secrets
        env_example = Path(".env.example")
        if env_example.exists():
            from app.security.secret_scanner import get_secret_scanner
            scanner = get_secret_scanner()
            content = env_example.read_text(encoding="utf-8")
            result = scanner.scan(content)
            if result.has_secrets:
                for finding in result.findings[:5]:
                    findings.append(AuditFinding(
                        id=f"secret_in_env_example_{finding.pattern_name}",
                        title=f"Potential secret in .env.example ({finding.pattern_name})",
                        severity=AuditSeverity.MEDIUM,
                        category=AuditCategory.SECRET,
                        description=f"Found {finding.secret_type.value} pattern in .env.example",
                        remediation="Remove real secrets from .env.example, use placeholder values",
                        affected_component=".env.example",
                    ))

        # Check if .env file has restricted permissions (Unix only)
        env_file = Path(".env")
        if env_file.exists():
            try:
                import stat
                mode = env_file.stat().st_mode
                if mode & stat.S_IROTH:  # Readable by others
                    findings.append(AuditFinding(
                        id="secret_env_world_readable",
                        title=".env file is world-readable",
                        severity=AuditSeverity.HIGH,
                        category=AuditCategory.SECRET,
                        description=".env file permissions allow other users to read secrets",
                        remediation="Run: chmod 600 .env",
                        affected_component=".env",
                        auto_fixable=True,
                    ))
            except (OSError, AttributeError):
                pass  # Windows or permission error

        return findings

    async def _audit_memory(self) -> list[AuditFinding]:
        """Deep mode: Audit memory system configuration."""
        findings: list[AuditFinding] = []

        # Check if workspace memory files exist and are accessible
        memory_dir = Path("workspace_data")
        if memory_dir.exists():
            memory_files = list(memory_dir.glob("*.md"))
            if memory_files:
                # Check for potentially sensitive content in memory files
                for mf in memory_files[:5]:
                    try:
                        content = mf.read_text(encoding="utf-8")
                        if len(content) > 100_000:
                            findings.append(AuditFinding(
                                id=f"memory_large_file_{mf.name}",
                                title=f"Memory file '{mf.name}' is very large",
                                severity=AuditSeverity.LOW,
                                category=AuditCategory.MEMORY,
                                description=f"Memory file is {len(content)} chars — may slow context injection",
                                remediation="Consider pruning old entries from memory files",
                                affected_component=str(mf),
                            ))
                    except Exception:
                        pass

        return findings


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_runner: Optional[SecurityAuditRunner] = None


def get_security_audit_runner() -> SecurityAuditRunner:
    """Get the singleton SecurityAuditRunner instance."""
    global _runner
    if _runner is None:
        _runner = SecurityAuditRunner()
    return _runner
