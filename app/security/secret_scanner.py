"""
Nexus - Secret Scanner (OpenClaw-inspired).

Detect secrets/credentials in tool outputs and LLM responses
before they are logged or sent to external services.

Inspired by:
- OpenClaw .detect-secrets.cfg — secret detection in CI/CD
- OpenClaw src/security/audit-secrets — runtime secret scanning

Features:
- Pattern-based detection (API keys, tokens, passwords, connection strings)
- Entropy-based detection for high-entropy strings
- Redaction of detected secrets in logs
- Configurable sensitivity levels
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class SecretType(str, Enum):
    """Types of secrets that can be detected."""
    API_KEY = "api_key"
    TOKEN = "token"
    PASSWORD = "password"
    CONNECTION_STRING = "connection_string"
    PRIVATE_KEY = "private_key"
    AWS_KEY = "aws_key"
    JWT = "jwt"
    GENERIC_SECRET = "generic_secret"
    HIGH_ENTROPY = "high_entropy"


class Severity(str, Enum):
    """Severity of secret finding."""
    CRITICAL = "critical"   # Known provider API keys
    HIGH = "high"           # Passwords, private keys
    MEDIUM = "medium"       # Generic tokens, connection strings
    LOW = "low"             # High entropy strings (may be false positive)


@dataclass
class SecretFinding:
    """A detected secret in text."""
    secret_type: SecretType
    severity: Severity
    line_number: int = 0
    start_pos: int = 0
    end_pos: int = 0
    redacted_value: str = ""   # First 4 + *** + last 4
    pattern_name: str = ""
    context: str = ""          # Surrounding text (redacted)


@dataclass
class ScanResult:
    """Result of scanning text for secrets."""
    has_secrets: bool = False
    findings: list[SecretFinding] = field(default_factory=list)
    redacted_text: str = ""
    scan_time_ms: float = 0.0

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)


# ─────────────────────────────────────────────────────────────────
# Secret Patterns
# ─────────────────────────────────────────────────────────────────

@dataclass
class SecretPattern:
    """A pattern for detecting a specific type of secret."""
    name: str
    regex: re.Pattern
    secret_type: SecretType
    severity: Severity


# Compiled patterns for various secret types
SECRET_PATTERNS: list[SecretPattern] = [
    # AWS
    SecretPattern(
        name="aws_access_key",
        regex=re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}", re.IGNORECASE),
        secret_type=SecretType.AWS_KEY,
        severity=Severity.CRITICAL,
    ),
    SecretPattern(
        name="aws_secret_key",
        regex=re.compile(r"(?:aws_secret_access_key|aws_secret)\s*[=:]\s*[A-Za-z0-9/+=]{40}", re.IGNORECASE),
        secret_type=SecretType.AWS_KEY,
        severity=Severity.CRITICAL,
    ),
    # OpenAI
    SecretPattern(
        name="openai_api_key",
        regex=re.compile(r"sk-[A-Za-z0-9]{20,}T3BlbkFJ[A-Za-z0-9]{20,}|sk-proj-[A-Za-z0-9_-]{40,}"),
        secret_type=SecretType.API_KEY,
        severity=Severity.CRITICAL,
    ),
    # Anthropic
    SecretPattern(
        name="anthropic_api_key",
        regex=re.compile(r"sk-ant-[A-Za-z0-9_-]{40,}"),
        secret_type=SecretType.API_KEY,
        severity=Severity.CRITICAL,
    ),
    # Google
    SecretPattern(
        name="google_api_key",
        regex=re.compile(r"AIza[A-Za-z0-9_-]{35}"),
        secret_type=SecretType.API_KEY,
        severity=Severity.CRITICAL,
    ),
    # GitHub
    SecretPattern(
        name="github_token",
        regex=re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"),
        secret_type=SecretType.TOKEN,
        severity=Severity.CRITICAL,
    ),
    # Slack
    SecretPattern(
        name="slack_token",
        regex=re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
        secret_type=SecretType.TOKEN,
        severity=Severity.HIGH,
    ),
    # Discord
    SecretPattern(
        name="discord_token",
        regex=re.compile(r"[MN][A-Za-z\d]{23,}\.[\w-]{6}\.[\w-]{27,}"),
        secret_type=SecretType.TOKEN,
        severity=Severity.HIGH,
    ),
    # JWT
    SecretPattern(
        name="jwt_token",
        regex=re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        secret_type=SecretType.JWT,
        severity=Severity.MEDIUM,
    ),
    # Private keys
    SecretPattern(
        name="private_key",
        regex=re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
        secret_type=SecretType.PRIVATE_KEY,
        severity=Severity.CRITICAL,
    ),
    # Generic passwords in config
    SecretPattern(
        name="password_assignment",
        regex=re.compile(
            r"(?:password|passwd|pwd|secret|token|api_key|apikey|auth_token)\s*[=:]\s*['\"][^'\"]{8,}['\"]",
            re.IGNORECASE,
        ),
        secret_type=SecretType.PASSWORD,
        severity=Severity.HIGH,
    ),
    # Connection strings
    SecretPattern(
        name="connection_string",
        regex=re.compile(
            r"(?:postgres(?:ql)?|mysql|mongodb|redis|amqp)://[^\s\"']+:[^\s\"']+@[^\s\"']+",
            re.IGNORECASE,
        ),
        secret_type=SecretType.CONNECTION_STRING,
        severity=Severity.HIGH,
    ),
    # Stripe
    SecretPattern(
        name="stripe_key",
        regex=re.compile(r"(?:sk|pk)_(?:test|live)_[A-Za-z0-9]{20,}"),
        secret_type=SecretType.API_KEY,
        severity=Severity.CRITICAL,
    ),
    # SendGrid
    SecretPattern(
        name="sendgrid_key",
        regex=re.compile(r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"),
        secret_type=SecretType.API_KEY,
        severity=Severity.HIGH,
    ),
    # Telegram Bot Token
    SecretPattern(
        name="telegram_bot_token",
        regex=re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"),
        secret_type=SecretType.TOKEN,
        severity=Severity.HIGH,
    ),
]


class SecretScanner:
    """
    Scan text for secrets and credentials.

    Usage:
        scanner = get_secret_scanner()
        result = scanner.scan(tool_output)
        if result.has_secrets:
            # Use result.redacted_text instead of raw output
            log.warning("secrets_detected", count=len(result.findings))
    """

    def __init__(
        self,
        entropy_threshold: float = 4.5,
        min_entropy_length: int = 20,
        max_entropy_length: int = 200,
        enable_entropy: bool = True,
    ) -> None:
        self._entropy_threshold = entropy_threshold
        self._min_entropy_length = min_entropy_length
        self._max_entropy_length = max_entropy_length
        self._enable_entropy = enable_entropy
        self._scan_count = 0
        self._findings_count = 0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "total_scans": self._scan_count,
            "total_findings": self._findings_count,
        }

    def scan(self, text: str, redact: bool = True) -> ScanResult:
        """Scan text for secrets.

        Args:
            text: Text to scan (tool output, config, etc.).
            redact: Whether to generate redacted version.

        Returns:
            ScanResult with findings and optionally redacted text.
        """
        import time
        t0 = time.perf_counter()
        self._scan_count += 1

        findings: list[SecretFinding] = []

        # Pattern-based detection
        for pattern in SECRET_PATTERNS:
            for match in pattern.regex.finditer(text):
                value = match.group(0)
                finding = SecretFinding(
                    secret_type=pattern.secret_type,
                    severity=pattern.severity,
                    start_pos=match.start(),
                    end_pos=match.end(),
                    redacted_value=self._redact_value(value),
                    pattern_name=pattern.name,
                    context=self._extract_context(text, match.start(), match.end()),
                )
                findings.append(finding)

        # Entropy-based detection
        if self._enable_entropy:
            entropy_findings = self._scan_entropy(text)
            # Don't add entropy findings that overlap with pattern findings
            for ef in entropy_findings:
                if not any(
                    self._overlaps(ef, f) for f in findings
                ):
                    findings.append(ef)

        # Sort by position
        findings.sort(key=lambda f: f.start_pos)

        # Generate redacted text
        redacted = self._redact_text(text, findings) if redact and findings else text

        self._findings_count += len(findings)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        result = ScanResult(
            has_secrets=len(findings) > 0,
            findings=findings,
            redacted_text=redacted,
            scan_time_ms=round(elapsed_ms, 2),
        )

        if findings:
            logger.warning(
                "secrets_detected",
                count=len(findings),
                critical=result.critical_count,
                high=result.high_count,
                patterns=[f.pattern_name for f in findings[:5]],
            )

        return result

    def scan_and_redact(self, text: str) -> str:
        """Convenience: scan and return redacted text."""
        result = self.scan(text, redact=True)
        return result.redacted_text

    def _scan_entropy(self, text: str) -> list[SecretFinding]:
        """Scan for high-entropy strings (potential secrets)."""
        findings: list[SecretFinding] = []

        # Split into tokens (space, newline, common delimiters)
        token_pattern = re.compile(r"[A-Za-z0-9+/=_-]{%d,%d}" % (
            self._min_entropy_length, self._max_entropy_length,
        ))

        for match in token_pattern.finditer(text):
            token = match.group(0)
            entropy = self._shannon_entropy(token)
            if entropy >= self._entropy_threshold:
                findings.append(SecretFinding(
                    secret_type=SecretType.HIGH_ENTROPY,
                    severity=Severity.LOW,
                    start_pos=match.start(),
                    end_pos=match.end(),
                    redacted_value=self._redact_value(token),
                    pattern_name="high_entropy",
                    context=self._extract_context(text, match.start(), match.end()),
                ))

        return findings

    @staticmethod
    def _shannon_entropy(data: str) -> float:
        """Calculate Shannon entropy of a string."""
        if not data:
            return 0.0
        entropy = 0.0
        length = len(data)
        char_counts: dict[str, int] = {}
        for char in data:
            char_counts[char] = char_counts.get(char, 0) + 1
        for count in char_counts.values():
            probability = count / length
            if probability > 0:
                entropy -= probability * math.log2(probability)
        return entropy

    @staticmethod
    def _redact_value(value: str) -> str:
        """Redact a secret value, keeping first 4 and last 4 chars."""
        if len(value) <= 12:
            return value[:2] + "***" + value[-2:]
        return value[:4] + "***" + value[-4:]

    @staticmethod
    def _extract_context(text: str, start: int, end: int, context_chars: int = 20) -> str:
        """Extract context around a finding (redacted)."""
        ctx_start = max(0, start - context_chars)
        ctx_end = min(len(text), end + context_chars)
        before = text[ctx_start:start]
        after = text[end:ctx_end]
        return f"...{before}[REDACTED]{after}..."

    def _redact_text(self, text: str, findings: list[SecretFinding]) -> str:
        """Replace all found secrets with [REDACTED] in text."""
        if not findings:
            return text

        # Process from end to start to maintain positions
        result = text
        for finding in sorted(findings, key=lambda f: f.start_pos, reverse=True):
            result = (
                result[:finding.start_pos]
                + "[REDACTED]"
                + result[finding.end_pos:]
            )
        return result

    @staticmethod
    def _overlaps(a: SecretFinding, b: SecretFinding) -> bool:
        """Check if two findings overlap in position."""
        return not (a.end_pos <= b.start_pos or b.end_pos <= a.start_pos)


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_scanner: Optional[SecretScanner] = None


def get_secret_scanner() -> SecretScanner:
    """Get the singleton SecretScanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = SecretScanner()
    return _scanner
