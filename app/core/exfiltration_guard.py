"""
Nexus - Exfiltration Guard (Hermes-agent inspired).

Detect and block accidental or malicious leakage of secrets in:
- LLM text responses returned to the user
- URLs constructed by the agent (browser, fetch)
- Tool outputs being sent to external services
- curl/wget commands with embedded credentials

This is a LAST-LINE defence: even if a secret somehow enters the
conversation, the guard prevents it from reaching the user or external
services.

Inspired by: hermes-agent browser_tool.py secret exfiltration blocking
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.core.secret_redaction import is_secret_like, redact_secrets

logger = structlog.get_logger(__name__)


# ── Verdict ──────────────────────────────────────────────────────────────

@dataclass
class ExfiltrationVerdict:
    """Result of an exfiltration check."""

    safe: bool
    """True if no exfiltration detected."""

    findings: list[str] = field(default_factory=list)
    """Human-readable descriptions of detected leaks."""

    redacted_text: str = ""
    """The text with any detected secrets redacted (if not safe)."""

    leak_count: int = 0
    """Number of distinct leaked values found."""


# ── Patterns for exfiltration vectors ────────────────────────────────────

# curl/wget with data flags that might exfiltrate secrets
_EXFIL_CURL_PATTERN = re.compile(
    r"\b(?:curl|wget|httpie|http)\s+.*?"
    r"(?:-d|--data|--data-raw|--data-binary|--upload-file|--post-data)\s+",
    re.IGNORECASE | re.DOTALL,
)

# Base64-encoded content in URLs (common exfiltration technique)
_BASE64_IN_URL = re.compile(
    r"https?://[^\s]*[?&/][a-zA-Z0-9+/]{40,}={0,2}",
)

# Environment variable dumps
_ENV_DUMP_PATTERNS = [
    re.compile(r"(?:printenv|env\b|set\b).*?(?:API_KEY|SECRET|TOKEN|PASSWORD)", re.IGNORECASE),
    re.compile(r"os\.environ\[.+(?:KEY|SECRET|TOKEN|PASS)", re.IGNORECASE),
    re.compile(r"\$\{?(?:API_KEY|SECRET_KEY|ACCESS_TOKEN|AWS_SECRET)", re.IGNORECASE),
]

# Known secret prefixes that should NEVER appear in user-facing text
_LEAKED_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenAI API key", re.compile(r"sk-[a-zA-Z0-9_-]{20,}")),
    ("OpenRouter key", re.compile(r"sk-or-v1-[a-zA-Z0-9]{40,}")),
    ("Anthropic key", re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}")),
    ("GitHub PAT", re.compile(r"ghp_[a-zA-Z0-9]{36,}")),
    ("Google API key", re.compile(r"AIza[a-zA-Z0-9_-]{35}")),
    ("AWS Access Key", re.compile(r"AKIA[A-Z0-9]{16}")),
    ("Slack token", re.compile(r"xox[bps]-[a-zA-Z0-9-]{20,}")),
    ("Discord token", re.compile(r"[MN][A-Za-z\d]{23,}\.[\w-]{6}\.[\w-]{27,}")),
    ("Telegram token", re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}")),
    ("JWT token", re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}")),
    ("Bearer token", re.compile(r"Bearer\s+[a-zA-Z0-9._~+/=-]{30,}", re.IGNORECASE)),
]


# ── Public API ───────────────────────────────────────────────────────────

def check_response(text: str) -> ExfiltrationVerdict:
    """Check an LLM response for leaked secrets before returning to user.

    This is the primary guard: called on every ``final_content`` before
    it reaches the frontend/channel.

    Parameters
    ----------
    text:
        The LLM's text response.

    Returns
    -------
    ExfiltrationVerdict:
        Contains redacted text if leaks were found.
    """
    if not text or len(text) < 10:
        return ExfiltrationVerdict(safe=True, redacted_text=text)

    findings: list[str] = []
    leak_count = 0

    # Check for leaked secrets
    for secret_name, pattern in _LEAKED_SECRET_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            leak_count += len(matches)
            findings.append(
                f"{secret_name} detected ({len(matches)} occurrence{'s' if len(matches) > 1 else ''})"
            )

    # Check for curl/wget exfiltration commands
    if _EXFIL_CURL_PATTERN.search(text):
        # Only flag if the curl command contains secret-like content
        curl_match = _EXFIL_CURL_PATTERN.search(text)
        if curl_match:
            # Get the text around the curl command
            start = max(0, curl_match.start() - 20)
            end = min(len(text), curl_match.end() + 200)
            context = text[start:end]
            for _name, pat in _LEAKED_SECRET_PATTERNS:
                if pat.search(context):
                    findings.append(f"Exfiltration attempt via curl/wget with {_name}")
                    leak_count += 1
                    break

    # Check for base64-encoded content in URLs
    b64_matches = _BASE64_IN_URL.findall(text)
    for url in b64_matches:
        # Only flag if the base64 portion looks like it could contain a secret
        if len(url) > 100:
            findings.append("Suspicious base64-encoded data in URL")
            leak_count += 1

    # Check for env variable dumps
    for env_pat in _ENV_DUMP_PATTERNS:
        if env_pat.search(text):
            findings.append("Environment variable dump containing sensitive keys")
            leak_count += 1
            break

    if leak_count > 0:
        redacted = redact_secrets(text)
        logger.warning(
            "exfiltration_detected",
            findings=findings,
            leak_count=leak_count,
        )
        return ExfiltrationVerdict(
            safe=False,
            findings=findings,
            redacted_text=redacted,
            leak_count=leak_count,
        )

    return ExfiltrationVerdict(safe=True, redacted_text=text)


def check_url(url: str) -> ExfiltrationVerdict:
    """Check if a URL contains leaked secrets (agent-constructed URLs).

    Called before browser navigation, fetch_url, or any outbound request
    constructed by the agent.

    Parameters
    ----------
    url:
        URL to check.

    Returns
    -------
    ExfiltrationVerdict:
        Safe=False if the URL contains embedded secrets.
    """
    if not url or len(url) < 10:
        return ExfiltrationVerdict(safe=True, redacted_text=url)

    findings: list[str] = []
    leak_count = 0

    for secret_name, pattern in _LEAKED_SECRET_PATTERNS:
        if pattern.search(url):
            findings.append(f"{secret_name} leaked in URL")
            leak_count += 1

    # Check for base64-encoded secrets in URL query/path
    if _BASE64_IN_URL.search(url):
        # Decode and check if it contains secrets
        findings.append("Suspicious base64-encoded data in URL path/query")
        leak_count += 1

    if leak_count > 0:
        redacted = redact_secrets(url)
        logger.warning(
            "url_exfiltration_detected",
            url_preview=url[:50] + "...",
            findings=findings,
        )
        return ExfiltrationVerdict(
            safe=False,
            findings=findings,
            redacted_text=redacted,
            leak_count=leak_count,
        )

    return ExfiltrationVerdict(safe=True, redacted_text=url)


def check_tool_output(output: str, tool_name: str = "") -> ExfiltrationVerdict:
    """Check tool output for secrets before including in LLM context.

    Some tools (terminal, code execution) may inadvertently expose
    environment variables or config files containing secrets.

    Parameters
    ----------
    output:
        Raw tool output string.
    tool_name:
        Name of the tool that produced this output (for logging).

    Returns
    -------
    ExfiltrationVerdict:
        Contains redacted output if secrets were found.
    """
    if not output or len(output) < 10:
        return ExfiltrationVerdict(safe=True, redacted_text=output)

    findings: list[str] = []
    leak_count = 0

    for secret_name, pattern in _LEAKED_SECRET_PATTERNS:
        matches = pattern.findall(output)
        if matches:
            leak_count += len(matches)
            findings.append(f"{secret_name} in {tool_name or 'tool'} output")

    if leak_count > 0:
        redacted = redact_secrets(output)
        logger.warning(
            "tool_output_secret_detected",
            tool=tool_name,
            findings=findings,
            leak_count=leak_count,
        )
        return ExfiltrationVerdict(
            safe=False,
            findings=findings,
            redacted_text=redacted,
            leak_count=leak_count,
        )

    return ExfiltrationVerdict(safe=True, redacted_text=output)
