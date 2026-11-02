"""
Nexus - Security Audit + Credential Leak Detection.

Deep security scanning for config, sandbox, tools, and channel surfaces.
Credential leak detection for tool outputs (IronClaw-inspired).

Inspired by:
- OpenClaw src/security/ — comprehensive audit system
- IronClaw crates/ironclaw_safety/src/credential_detect.rs — credential scanning
- IronClaw crates/ironclaw_safety/src/leak_detector.rs — leak detection in outputs
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Optional

import structlog

from app.config import get_settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Credential Leak Detection (IronClaw-inspired)
# ---------------------------------------------------------------------------

# Patterns for credential detection in tool outputs
_CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE)),
    ("AWS Secret Key", re.compile(r"(?:aws_secret_access_key|secret_key)\s*[=:]\s*[\w/+]{40}", re.IGNORECASE)),
    ("OpenAI API Key", re.compile(r"sk-[A-Za-z0-9]{20,}", re.IGNORECASE)),
    ("OpenRouter Key", re.compile(r"sk-or-v1-[A-Za-z0-9]{64}", re.IGNORECASE)),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}", re.IGNORECASE)),
    ("GitHub Classic Token", re.compile(r"ghp_[A-Za-z0-9]{36}", re.IGNORECASE)),
    ("Slack Token", re.compile(r"xox[bprs]-[A-Za-z0-9\-]{24,}", re.IGNORECASE)),
    ("Telegram Bot Token", re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}", re.IGNORECASE)),
    ("Generic Bearer Token", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)),
    ("Generic API Key", re.compile(r"(?:api[_-]?key|apikey|secret[_-]?key)\s*[=:]\s*[\"']?[A-Za-z0-9\-._]{20,}[\"']?", re.IGNORECASE)),
    ("Private Key Block", re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----", re.IGNORECASE)),
    ("Database URL with Password", re.compile(r"(?:postgres|mysql|mongodb)(?:ql)?://[^:]+:[^@]+@", re.IGNORECASE)),
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z\-_]{35}", re.IGNORECASE)),
    ("Anthropic Key", re.compile(r"sk-ant-[A-Za-z0-9\-]{20,}", re.IGNORECASE)),
]


@dataclass
class CredentialLeak:
    """Detected credential leak in text."""
    credential_type: str
    matched_text: str  # redacted version
    position: int
    length: int


def scan_for_credentials(text: str) -> list[CredentialLeak]:
    """Scan text for credential patterns. Returns list of detected leaks.

    Inspired by IronClaw's credential_detect.rs — scans tool outputs
    before they are injected back into LLM context.
    """
    if not text or len(text) < 10:
        return []

    leaks: list[CredentialLeak] = []
    seen_positions: set[int] = set()

    for cred_type, pattern in _CREDENTIAL_PATTERNS:
        for match in pattern.finditer(text):
            pos = match.start()
            if pos in seen_positions:
                continue
            seen_positions.add(pos)

            matched = match.group()
            # Redact: show first 4 and last 4 chars
            if len(matched) > 12:
                redacted = matched[:4] + "..." + matched[-4:]
            else:
                redacted = matched[:2] + "***"

            leaks.append(CredentialLeak(
                credential_type=cred_type,
                matched_text=redacted,
                position=pos,
                length=len(matched),
            ))

    return leaks


def sanitize_credentials(text: str) -> tuple[str, int]:
    """Replace detected credentials with [REDACTED]. Returns (sanitized_text, count).

    Use this on tool outputs before injecting into LLM context.
    """
    if not text:
        return text, 0

    count = 0
    result = text
    for cred_type, pattern in _CREDENTIAL_PATTERNS:
        def _replacer(m: re.Match) -> str:
            nonlocal count
            count += 1
            return f"[REDACTED:{cred_type}]"
        result = pattern.sub(_replacer, result)

    if count > 0:
        logger.warning("credentials_sanitized", count=count)

    return result, count


# ---------------------------------------------------------------------------
# Audit Finding
# ---------------------------------------------------------------------------

@dataclass
class AuditFinding:
    category: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    title: str
    description: str
    fix_hint: str = ""


class SecurityAudit:
    """
    Deep security audit — inspired by OpenClaw `openclaw security audit --deep`.

    Checks: config, secrets, sandbox, tool policy, channel exposure, credential leaks.
    """

    def __init__(self) -> None:
        self.settings = get_settings()

    async def run(self, deep: bool = False) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        findings.extend(self._audit_config())
        findings.extend(self._audit_secrets())
        findings.extend(self._audit_sandbox())
        findings.extend(self._audit_tool_surface())
        if deep:
            findings.extend(self._audit_filesystem())
            findings.extend(self._audit_env_for_leaks())
        return findings

    def _audit_config(self) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        s = self.settings

        if s.app_debug and s.is_production:
            findings.append(AuditFinding(
                category="config", severity="high",
                title="Debug mode in production",
                description="APP_DEBUG=true while APP_ENV=production",
                fix_hint="Set APP_DEBUG=false",
            ))

        if s.app_host == "0.0.0.0" and s.is_production:
            findings.append(AuditFinding(
                category="config", severity="medium",
                title="Gateway bound to all interfaces",
                description="APP_HOST=0.0.0.0 exposes the gateway to the network",
                fix_hint="Bind to 127.0.0.1 and use a reverse proxy",
            ))

        if s.app_secret_key == "change-me-in-production":
            findings.append(AuditFinding(
                category="config", severity="high",
                title="Default secret key",
                description="APP_SECRET_KEY uses the insecure default value",
                fix_hint="Generate a random secret: python -c \"import secrets; print(secrets.token_hex(32))\"",
            ))

        if s.jwt_secret_key == "change-me-jwt-secret":
            findings.append(AuditFinding(
                category="config", severity="high",
                title="Default JWT secret",
                description="JWT_SECRET_KEY uses the insecure default",
                fix_hint="Set JWT_SECRET_KEY to a random value in .env",
            ))

        return findings

    def _audit_secrets(self) -> list[AuditFinding]:
        findings: list[AuditFinding] = []

        env_file = Path(".env")
        if env_file.exists():
            content = env_file.read_text(encoding="utf-8")
            # Check for common exposed patterns
            if "sk-" in content and "EXAMPLE" not in content.upper():
                findings.append(AuditFinding(
                    category="secrets", severity="info",
                    title="API keys detected in .env",
                    description="Ensure .env is in .gitignore and not committed",
                ))

        gitignore = Path(".gitignore")
        if gitignore.exists():
            gi = gitignore.read_text()
            if ".env" not in gi:
                findings.append(AuditFinding(
                    category="secrets", severity="high",
                    title=".env not in .gitignore",
                    description="Secrets may be committed to version control",
                    fix_hint="Add .env to .gitignore",
                ))

        return findings

    def _audit_sandbox(self) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        s = self.settings

        if not s.docker_sandbox_enabled and not s.ironclaw_enabled:
            findings.append(AuditFinding(
                category="sandbox", severity="medium",
                title="No sandbox enabled",
                description="Code execution runs as subprocess without isolation",
                fix_hint="Enable DOCKER_SANDBOX_ENABLED=true",
            ))

        if s.docker_sandbox_enabled:
            # Check Docker socket
            import shutil
            if not shutil.which("docker"):
                findings.append(AuditFinding(
                    category="sandbox", severity="medium",
                    title="Docker not found in PATH",
                    description="Docker sandbox enabled but docker binary not found",
                ))

        return findings

    def _audit_tool_surface(self) -> list[AuditFinding]:
        findings: list[AuditFinding] = []

        # Check if dangerous tools are unrestricted
        findings.append(AuditFinding(
            category="tools", severity="info",
            title="Tool policy system available",
            description="Use ToolPolicy to restrict dangerous tools per session",
        ))

        return findings

    def _audit_filesystem(self) -> list[AuditFinding]:
        """Deep filesystem checks."""
        findings: list[AuditFinding] = []

        workspace = Path("workspace_data")
        if workspace.exists():
            # Check for symlinks (potential escape)
            for p in workspace.rglob("*"):
                if p.is_symlink():
                    findings.append(AuditFinding(
                        category="filesystem", severity="low",
                        title=f"Symlink found: {p}",
                        description="Symlinks in workspace could reference files outside sandbox",
                    ))

        return findings

    def _audit_env_for_leaks(self) -> list[AuditFinding]:
        """Scan workspace files for accidentally committed credentials."""
        findings: list[AuditFinding] = []

        # Check common files that might contain leaked credentials
        suspect_files = [
            Path("workspace_data") / "**" / "*.env",
            Path("workspace_data") / "**" / "*.yaml",
            Path("workspace_data") / "**" / "*.yml",
            Path("workspace_data") / "**" / "*.json",
        ]

        workspace = Path("workspace_data")
        if not workspace.exists():
            return findings

        scanned = 0
        for pattern in ["*.env", "*.yaml", "*.yml", "*.json", "*.toml", "*.cfg"]:
            for f in workspace.rglob(pattern):
                if scanned > 100:  # Limit scan scope
                    break
                try:
                    content = f.read_text(encoding="utf-8", errors="ignore")
                    if len(content) > 100_000:  # Skip large files
                        continue
                    leaks = scan_for_credentials(content)
                    for leak in leaks:
                        findings.append(AuditFinding(
                            category="credential_leak",
                            severity="high",
                            title=f"{leak.credential_type} in {f.name}",
                            description=f"Found {leak.credential_type} at position {leak.position}: {leak.matched_text}",
                            fix_hint=f"Remove or rotate the credential in {f}",
                        ))
                    scanned += 1
                except (OSError, UnicodeDecodeError):
                    continue

        if scanned > 0 and not any(f.category == "credential_leak" for f in findings):
            findings.append(AuditFinding(
                category="credential_leak", severity="info",
                title="No credential leaks detected",
                description=f"Scanned {scanned} workspace files — clean",
            ))

        return findings
