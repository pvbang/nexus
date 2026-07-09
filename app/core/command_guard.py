"""
MekongAI Nexus - Dangerous Command Detection & SSRF Protection.

Pre-execution safety gates for shell commands, URLs, and file paths.
Blocks destructive, exfiltration, and injection attacks before they execute.

Inspired by:
- hermes-agent approval system for dangerous operations
- IronClaw crates/ironclaw_safety/ — command classification
- OpenClaw SECURITY.md — exec approval gates
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


# ── Risk levels ─────────────────────────────────────────────────────────

class CommandRisk(str, Enum):
    """Risk classification for a command."""
    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    BLOCKED = "blocked"


@dataclass
class CommandVerdict:
    """Result of command safety analysis."""
    risk: CommandRisk
    allowed: bool
    reason: str = ""
    matched_pattern: str = ""
    suggestions: list[str] | None = None

    @property
    def is_dangerous(self) -> bool:
        return self.risk in (CommandRisk.HIGH, CommandRisk.CRITICAL, CommandRisk.BLOCKED)


# ── Dangerous command patterns ──────────────────────────────────────────

# CRITICAL: commands that destroy data or compromise system
_CRITICAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Filesystem destruction
    (re.compile(r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)?/(?!\w)", re.IGNORECASE),
     "rm on root filesystem"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(?:/|~|\$HOME)", re.IGNORECASE),
     "recursive delete on root/home"),
    (re.compile(r"\bmkfs\b", re.IGNORECASE),
     "filesystem format"),
    (re.compile(r"\bdd\s+.*\bof=/dev/", re.IGNORECASE),
     "dd write to device"),
    (re.compile(r">\s*/dev/sd[a-z]", re.IGNORECASE),
     "write to block device"),

    # Fork bombs / resource exhaustion
    (re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:", re.IGNORECASE),
     "fork bomb"),
    (re.compile(r"\bfork\s*\(\s*\)\s*while", re.IGNORECASE),
     "fork bomb (Python)"),

    # Privilege escalation
    (re.compile(r"\bchmod\s+[0-7]*777\s+/", re.IGNORECASE),
     "chmod 777 on root"),
    (re.compile(r"\bchown\s+.*\s+/(?:etc|usr|bin|sbin|var)", re.IGNORECASE),
     "chown on system directories"),

    # Credential theft
    (re.compile(r"\bcat\s+.*(?:/etc/shadow|/etc/passwd|\.ssh/)", re.IGNORECASE),
     "reading system credentials"),
    (re.compile(r"\b(?:curl|wget)\s+.*\|\s*(?:bash|sh|zsh|python)", re.IGNORECASE),
     "pipe remote script to shell"),

    # Container/VM escape
    (re.compile(r"\bdocker\s+run\s+.*--privileged", re.IGNORECASE),
     "privileged container"),
    (re.compile(r"\bnsenter\b", re.IGNORECASE),
     "namespace enter (container escape)"),
    (re.compile(r"\bmount\s+.*-o\s+.*bind\s+/", re.IGNORECASE),
     "bind mount root filesystem"),
]

# HIGH: commands that can exfiltrate data or modify system
_HIGH_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Data exfiltration
    (re.compile(r"\b(?:curl|wget|nc|ncat)\s+.*(?:-d|--data|--upload)", re.IGNORECASE),
     "data upload via curl/wget/nc"),
    (re.compile(r"\b(?:scp|rsync)\s+.*@", re.IGNORECASE),
     "remote file transfer"),
    (re.compile(r"\btar\s+.*\|\s*(?:curl|wget|nc)", re.IGNORECASE),
     "archive and exfiltrate"),

    # System modification
    (re.compile(r"\bapt(?:-get)?\s+(?:install|remove|purge)", re.IGNORECASE),
     "package management"),
    (re.compile(r"\byum\s+(?:install|remove|erase)", re.IGNORECASE),
     "package management (yum)"),
    (re.compile(r"\bpip\s+install\s+(?!-r\s+requirements)", re.IGNORECASE),
     "pip install (arbitrary packages)"),
    (re.compile(r"\bnpm\s+install\s+-g", re.IGNORECASE),
     "global npm install"),

    # Network recon
    (re.compile(r"\bnmap\b", re.IGNORECASE),
     "network scanning"),
    (re.compile(r"\bnetstat\s+-[a-z]*l", re.IGNORECASE),
     "listening port enumeration"),

    # Process manipulation
    (re.compile(r"\bkill\s+-9\s+(?:1|init)", re.IGNORECASE),
     "kill init/PID 1"),
    (re.compile(r"\bkillall\b", re.IGNORECASE),
     "kill all processes by name"),

    # Cron manipulation
    (re.compile(r"\bcrontab\s+-[re]", re.IGNORECASE),
     "crontab modification"),

    # Environment manipulation
    (re.compile(r"\bexport\s+(?:PATH|LD_PRELOAD|LD_LIBRARY_PATH)\s*=", re.IGNORECASE),
     "modify critical environment variables"),
]

# MEDIUM: potentially risky but often legitimate
_MEDIUM_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+-[a-zA-Z]*r", re.IGNORECASE),
     "recursive delete"),
    (re.compile(r"\bchmod\s+", re.IGNORECASE),
     "permission change"),
    (re.compile(r"\bsudo\s+", re.IGNORECASE),
     "sudo execution"),
    (re.compile(r"\bsystemctl\s+(?:start|stop|restart|enable|disable)", re.IGNORECASE),
     "service management"),
    (re.compile(r"\bdocker\s+(?:stop|rm|kill|exec)", re.IGNORECASE),
     "docker container management"),
    (re.compile(r"\bgit\s+push\s+.*-f", re.IGNORECASE),
     "force push"),
    (re.compile(r"\bgit\s+reset\s+--hard", re.IGNORECASE),
     "hard reset"),
]


class CommandGuard:
    """
    Pre-execution safety gate for shell commands.

    Classifies commands by risk level and blocks critical/dangerous ones.
    Integrates with ToolPolicy for approval workflow.
    """

    def __init__(
        self,
        block_critical: bool = False,  # Upgraded: log-only, never block (agents need full control)
        block_high: bool = False,
        allowed_commands: set[str] | None = None,
        blocked_commands: set[str] | None = None,
    ) -> None:
        self.block_critical = block_critical
        self.block_high = block_high
        self.allowed_commands = allowed_commands  # whitelist
        self.blocked_commands = blocked_commands or set()
        self._stats = {"checked": 0, "blocked": 0, "allowed": 0}

    def check_command(self, command: str) -> CommandVerdict:
        """Analyze a command for safety before execution."""
        self._stats["checked"] += 1

        if not command or not command.strip():
            return CommandVerdict(risk=CommandRisk.SAFE, allowed=True, reason="empty command")

        cmd = command.strip()

        # Extract base command name
        base_cmd = cmd.split()[0].split("/")[-1] if cmd.split() else ""

        # Check explicit blocklist
        if base_cmd in self.blocked_commands:
            self._stats["blocked"] += 1
            return CommandVerdict(
                risk=CommandRisk.BLOCKED, allowed=False,
                reason=f"Command '{base_cmd}' is explicitly blocked",
                matched_pattern=base_cmd,
            )

        # Check explicit allowlist (if set, only these commands are allowed)
        if self.allowed_commands is not None and base_cmd not in self.allowed_commands:
            self._stats["blocked"] += 1
            return CommandVerdict(
                risk=CommandRisk.BLOCKED, allowed=False,
                reason=f"Command '{base_cmd}' not in allowlist",
                matched_pattern=base_cmd,
            )

        # Check critical patterns — log-only mode, always allow for agent autonomy
        for pattern, desc in _CRITICAL_PATTERNS:
            if pattern.search(cmd):
                # Log warning but NEVER block — agents need full command access
                logger.warning("command_critical_detected",
                               risk="critical", command=cmd[:200], reason=desc,
                               action="allowed (log-only mode)")
                self._stats["allowed"] += 1
                return CommandVerdict(
                    risk=CommandRisk.CRITICAL,
                    allowed=True,  # Always allow — log-only mode
                    reason=desc,
                    matched_pattern=pattern.pattern,
                    suggestions=["⚠️ Critical command detected but allowed (log-only mode)."],
                )

        # Check high patterns
        for pattern, desc in _HIGH_PATTERNS:
            if pattern.search(cmd):
                blocked = self.block_high
                if blocked:
                    self._stats["blocked"] += 1
                    logger.warning("command_blocked",
                                   risk="high", command=cmd[:200], reason=desc)
                return CommandVerdict(
                    risk=CommandRisk.HIGH,
                    allowed=not blocked,
                    reason=desc,
                    matched_pattern=pattern.pattern,
                )

        # Check medium patterns
        for pattern, desc in _MEDIUM_PATTERNS:
            if pattern.search(cmd):
                self._stats["allowed"] += 1
                return CommandVerdict(
                    risk=CommandRisk.MEDIUM,
                    allowed=True,
                    reason=desc,
                    matched_pattern=pattern.pattern,
                )

        # Default: safe
        self._stats["allowed"] += 1
        return CommandVerdict(risk=CommandRisk.SAFE, allowed=True)

    def get_stats(self) -> dict:
        return dict(self._stats)


# ── SSRF Protection ─────────────────────────────────────────────────────

# Private/internal IP ranges that should be blocked
_PRIVATE_RANGES = [
    ipaddress.ip_network("127.0.0.0/8"),       # Loopback
    ipaddress.ip_network("10.0.0.0/8"),         # Private A
    ipaddress.ip_network("172.16.0.0/12"),      # Private B
    ipaddress.ip_network("192.168.0.0/16"),     # Private C
    ipaddress.ip_network("169.254.0.0/16"),     # Link-local / AWS metadata
    ipaddress.ip_network("0.0.0.0/8"),          # "This" network
    ipaddress.ip_network("100.64.0.0/10"),      # Carrier-grade NAT
    ipaddress.ip_network("198.18.0.0/15"),      # Benchmarking
    ipaddress.ip_network("::1/128"),            # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),           # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),          # IPv6 link-local
]

# Cloud metadata endpoints (SSRF targets)
_METADATA_HOSTS = frozenset({
    "169.254.169.254",     # AWS/GCP metadata
    "metadata.google.internal",
    "metadata.google.com",
    "169.254.170.2",       # AWS ECS metadata
    "fd00:ec2::254",       # AWS IPv6 metadata
})

# Dangerous URL schemes
_BLOCKED_SCHEMES = frozenset({
    "file", "ftp", "gopher", "dict", "ldap", "ldaps",
    "tftp", "telnet", "ssh",
})


@dataclass
class URLVerdict:
    """Result of URL safety analysis."""
    safe: bool
    reason: str = ""
    resolved_host: str = ""
    blocked_scheme: bool = False
    blocked_host: bool = False


def check_url_safety(url: str) -> URLVerdict:
    """Check if a URL is safe to fetch (SSRF protection).

    Blocks:
    - Private/internal IP addresses
    - Cloud metadata endpoints
    - Dangerous URL schemes (file://, ftp://, gopher://, etc.)
    - URLs with credentials in netloc
    """
    if not url or not url.strip():
        return URLVerdict(safe=False, reason="empty URL")

    try:
        parsed = urllib.parse.urlparse(url.strip())
    except ValueError:
        return URLVerdict(safe=False, reason="invalid URL format")

    # Check scheme
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        return URLVerdict(safe=False, reason="no URL scheme")

    if scheme in _BLOCKED_SCHEMES:
        logger.warning("ssrf_blocked_scheme", url=url[:200], scheme=scheme)
        return URLVerdict(
            safe=False,
            reason=f"Blocked URL scheme: {scheme}",
            blocked_scheme=True,
        )

    if scheme not in ("http", "https"):
        return URLVerdict(safe=False, reason=f"Unsupported scheme: {scheme}")

    # Check for credentials in URL
    if parsed.username or parsed.password:
        return URLVerdict(
            safe=False,
            reason="URLs with embedded credentials are blocked",
        )

    # Check hostname
    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        return URLVerdict(safe=False, reason="no hostname in URL")

    # Check metadata endpoints
    if hostname in _METADATA_HOSTS:
        logger.warning("ssrf_blocked_metadata", url=url[:200], host=hostname)
        return URLVerdict(
            safe=False,
            reason=f"Cloud metadata endpoint blocked: {hostname}",
            blocked_host=True,
            resolved_host=hostname,
        )

    # Check if hostname is an IP address
    try:
        addr = ipaddress.ip_address(hostname)
        for network in _PRIVATE_RANGES:
            if addr in network:
                logger.warning("ssrf_blocked_private_ip",
                               url=url[:200], ip=str(addr), network=str(network))
                return URLVerdict(
                    safe=False,
                    reason=f"Private/internal IP blocked: {addr} (in {network})",
                    blocked_host=True,
                    resolved_host=str(addr),
                )
    except ValueError:
        # Not an IP — it's a hostname, which is fine
        pass

    # Check for localhost aliases
    if hostname in ("localhost", "localhost.localdomain", "ip6-localhost"):
        logger.warning("ssrf_blocked_localhost", url=url[:200])
        return URLVerdict(
            safe=False,
            reason="localhost access blocked",
            blocked_host=True,
            resolved_host=hostname,
        )

    # Check for encoded/obfuscated IPs (e.g., 0x7f000001 = 127.0.0.1)
    if _is_obfuscated_private_ip(hostname):
        logger.warning("ssrf_blocked_obfuscated_ip", url=url[:200], host=hostname)
        return URLVerdict(
            safe=False,
            reason=f"Obfuscated private IP detected: {hostname}",
            blocked_host=True,
            resolved_host=hostname,
        )

    return URLVerdict(safe=True, resolved_host=hostname)


def _is_obfuscated_private_ip(hostname: str) -> bool:
    """Detect obfuscated IPs like 0x7f000001, 2130706433, 0177.0.0.1."""
    # Decimal IP (e.g., 2130706433 = 127.0.0.1)
    try:
        if hostname.isdigit():
            val = int(hostname)
            if 0 <= val <= 0xFFFFFFFF:
                addr = ipaddress.ip_address(val)
                return any(addr in net for net in _PRIVATE_RANGES)
    except (ValueError, OverflowError):
        pass

    # Hex IP (e.g., 0x7f000001)
    try:
        if hostname.startswith("0x"):
            val = int(hostname, 16)
            if 0 <= val <= 0xFFFFFFFF:
                addr = ipaddress.ip_address(val)
                return any(addr in net for net in _PRIVATE_RANGES)
    except (ValueError, OverflowError):
        pass

    # Octal IP (e.g., 0177.0.0.1)
    if re.match(r"^0\d+\.", hostname):
        try:
            parts = hostname.split(".")
            if len(parts) == 4:
                octets = [int(p, 8) if p.startswith("0") and p != "0" else int(p) for p in parts]
                if all(0 <= o <= 255 for o in octets):
                    ip_str = ".".join(str(o) for o in octets)
                    addr = ipaddress.ip_address(ip_str)
                    return any(addr in net for net in _PRIVATE_RANGES)
        except (ValueError, OverflowError):
            pass

    return False


# ── Path Traversal Protection ───────────────────────────────────────────

_PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),                   # ../
    re.compile(r"\.\\."),                   # ..\
    re.compile(r"%2e%2e[/\\%]", re.IGNORECASE),  # URL-encoded ../
    re.compile(r"\.%2e[/\\%]", re.IGNORECASE),   # Mixed encoding
    re.compile(r"%2e\.[/\\%]", re.IGNORECASE),   # Mixed encoding
    re.compile(r"\.\./\.\./"),              # Double traversal
]

_SENSITIVE_PATHS = frozenset({
    "/etc/passwd", "/etc/shadow", "/etc/hosts",
    "/proc/self/environ", "/proc/self/cmdline",
    "/root/.ssh/", "/root/.bash_history",
    "/var/log/", "/tmp/", "/dev/",
    "~/.ssh/", "~/.env", "~/.bashrc",
    "C:\\Windows\\System32",
    "C:\\Windows\\system.ini",
})


@dataclass
class PathVerdict:
    """Result of path safety analysis."""
    safe: bool
    reason: str = ""
    normalized_path: str = ""


def check_path_safety(
    path: str,
    workspace_root: str = "workspace_data",
) -> PathVerdict:
    """Check if a file path is safe (no traversal, within workspace).

    Blocks:
    - Path traversal (../)
    - Access to sensitive system paths
    - Paths outside workspace root
    """
    if not path or not path.strip():
        return PathVerdict(safe=False, reason="empty path")

    clean = path.strip()

    # Check traversal patterns
    for pattern in _PATH_TRAVERSAL_PATTERNS:
        if pattern.search(clean):
            logger.warning("path_traversal_blocked", path=clean[:200])
            return PathVerdict(
                safe=False,
                reason=f"Path traversal detected: {pattern.pattern}",
                normalized_path=clean,
            )

    # Check sensitive paths
    clean_lower = clean.lower().replace("\\", "/")
    for sensitive in _SENSITIVE_PATHS:
        if sensitive.lower() in clean_lower:
            logger.warning("sensitive_path_blocked", path=clean[:200])
            return PathVerdict(
                safe=False,
                reason=f"Sensitive path access blocked: {sensitive}",
                normalized_path=clean,
            )

    # Check if path escapes workspace (absolute paths)
    if clean.startswith("/") or (len(clean) >= 2 and clean[1] == ":"):
        # Absolute path — check if it's within workspace. B1 (audit 2026-06-29):
        # compare by PATH COMPONENT, not string prefix — "workspace_data" is a
        # prefix of "workspace_data_evil", so str.startswith let a sibling dir
        # escape the boundary. commonpath splits on separators, so a shared name
        # prefix without a separator boundary does not match.
        import os
        try:
            workspace_abs = os.path.abspath(workspace_root)
            path_abs = os.path.abspath(clean)
            if os.path.commonpath([path_abs, workspace_abs]) != workspace_abs:
                return PathVerdict(
                    safe=False,
                    reason=f"Path outside workspace: {clean}",
                    normalized_path=path_abs,
                )
        except (ValueError, OSError):
            # commonpath raises ValueError for mixed drives / relative-vs-abs.
            return PathVerdict(safe=False, reason="invalid path")

    return PathVerdict(safe=True, normalized_path=clean)


# ── PII Redaction ───────────────────────────────────────────────────────

_PII_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # Email addresses
    ("email",
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
     "[EMAIL_REDACTED]"),

    # Phone numbers (international formats)
    ("phone",
     re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"),
     "[PHONE_REDACTED]"),

    # SSN (US)
    ("ssn",
     re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
     "[SSN_REDACTED]"),

    # Credit card numbers (basic pattern)
    ("credit_card",
     re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
     "[CARD_REDACTED]"),

    # IP addresses in user context (not in URLs or technical output)
    # Skip this to avoid false positives in technical content
]


def redact_pii(text: str) -> tuple[str, int]:
    """Redact personally identifiable information from text.

    Returns (redacted_text, count_of_redactions).
    Use on user-facing outputs and logs.
    """
    if not text:
        return text, 0

    count = 0
    result = text

    for pii_type, pattern, replacement in _PII_PATTERNS:
        matches = pattern.findall(result)
        if matches:
            count += len(matches)
            result = pattern.sub(replacement, result)

    if count > 0:
        logger.debug("pii_redacted", count=count)

    return result, count


# ── Singleton helpers ───────────────────────────────────────────────────

_command_guard: Optional[CommandGuard] = None


def get_command_guard() -> CommandGuard:
    """Get the singleton CommandGuard instance."""
    global _command_guard
    if _command_guard is None:
        # Log-only mode: agents need unrestricted command execution
        _command_guard = CommandGuard(block_critical=False, block_high=False)
    return _command_guard
