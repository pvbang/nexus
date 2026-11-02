"""
Nexus - Secret Redaction for Logs & Outputs (Hermes-agent inspired).

Comprehensive secret detection and redaction to prevent API keys, tokens,
passwords, and other sensitive values from appearing in logs, error messages,
and user-facing outputs.

Provides:
- RedactingProcessor: structlog processor that filters secrets
- redact_secrets(): standalone function for any text
- is_secret_like(): heuristic check for secret-like strings

Inspired by: hermes-agent/agent/redact.py
"""

from __future__ import annotations

import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── Secret patterns (compiled for performance) ──────────────────────────

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # OpenAI / OpenRouter API keys
    ("openai_key", re.compile(r"sk-[a-zA-Z0-9_-]{20,}"), "[REDACTED_OPENAI_KEY]"),
    ("openrouter_key", re.compile(r"sk-or-v1-[a-zA-Z0-9]{40,}"), "[REDACTED_OPENROUTER_KEY]"),

    # Anthropic keys
    ("anthropic_key", re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"), "[REDACTED_ANTHROPIC_KEY]"),

    # GitHub tokens
    ("github_pat", re.compile(r"ghp_[a-zA-Z0-9]{36,}"), "[REDACTED_GITHUB_PAT]"),
    ("github_fine", re.compile(r"github_pat_[a-zA-Z0-9_]{22,}"), "[REDACTED_GITHUB_PAT]"),
    ("github_oauth", re.compile(r"gho_[a-zA-Z0-9]{36,}"), "[REDACTED_GITHUB_OAUTH]"),

    # Google / GCP keys
    ("google_api", re.compile(r"AIza[a-zA-Z0-9_-]{35}"), "[REDACTED_GOOGLE_KEY]"),

    # AWS keys — AKIA (long-term) AND ASIA (STS temporary). The old regex was
    # AKIA-only and leaked a live ASIA STS key (audit CC-6). Kept in sync with
    # app/security/secret_scanner.py's reference pattern.
    ("aws_access", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}"), "[REDACTED_AWS_KEY]"),
    ("aws_secret", re.compile(r"(?:aws_secret_access_key|secret_key)\s*[=:]\s*['\"]?([a-zA-Z0-9/+]{40})['\"]?", re.IGNORECASE),
     "[REDACTED_AWS_SECRET]"),

    # Slack tokens
    ("slack_bot", re.compile(r"xoxb-[a-zA-Z0-9-]{20,}"), "[REDACTED_SLACK_BOT]"),
    ("slack_app", re.compile(r"xapp-[a-zA-Z0-9-]{20,}"), "[REDACTED_SLACK_APP]"),
    ("slack_user", re.compile(r"xoxp-[a-zA-Z0-9-]{20,}"), "[REDACTED_SLACK_USER]"),

    # Discord tokens
    ("discord_token", re.compile(r"[MN][A-Za-z\d]{23,}\.[\w-]{6}\.[\w-]{27,}"), "[REDACTED_DISCORD_TOKEN]"),

    # Telegram bot tokens
    ("telegram_token", re.compile(r"\d{8,10}:[A-Za-z0-9_-]{35}"), "[REDACTED_TELEGRAM_TOKEN]"),

    # Bearer tokens
    ("bearer", re.compile(r"Bearer\s+[a-zA-Z0-9._~+/=-]{20,}", re.IGNORECASE), "[REDACTED_BEARER]"),

    # JWT tokens (3 dot-separated base64 segments)
    ("jwt", re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"), "[REDACTED_JWT]"),

    # Generic secret assignments in env/config
    ("env_secret", re.compile(
        r"(?:password|passwd|secret|token|api_key|apikey|auth_token|access_token|private_key)"
        r"\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?",
        re.IGNORECASE,
    ), "[REDACTED_SECRET]"),

    # URLs/connection-strings with embedded credentials (any scheme — http(s),
    # postgresql, redis, amqp, mongodb, …). Redact the whole userinfo@host so the
    # embedded password never lands in a log.
    ("url_creds", re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/\s]+:[^@/\s]+@[^\s]+"),
     "[REDACTED_URL_WITH_CREDS]"),

    # Hex-encoded secrets (64+ chars, common for app secrets)
    ("hex_secret", re.compile(
        r"(?:secret_key|app_secret|jwt_secret)\s*[=:]\s*['\"]?([a-f0-9]{32,})['\"]?",
        re.IGNORECASE,
    ), "[REDACTED_HEX_SECRET]"),
]

# Patterns for quick "is this secret-like?" heuristic
_SECRET_PREFIXES = frozenset({
    "sk-", "sk-or-", "sk-ant-", "ghp_", "gho_", "github_pat_",
    "AIza", "AKIA", "ASIA", "xoxb-", "xoxp-", "xapp-", "Bearer ",
    "eyJh",  # JWT header start
})

# ── Value-based redaction (CC-6) ────────────────────────────────────────
# Pattern-based detection can't catch an arbitrary low-entropy credential like
# the gateway password `AgyBedrock2026!` (no key=value form, entropy below the
# scanner threshold). The robust fix is value-based: load the secrets this
# deployment actually holds and redact those literals wherever they appear.
#
# Two sources, NOT a hardcoded variable list (which silently misses new secrets):
#   1. The pydantic Settings object — pydantic-settings reads .env into the OBJECT,
#      NOT into os.environ, so an env-only scan misses every .env-configured secret.
#      We scan model_dump() and treat any field whose NAME looks secret-bearing as
#      a credential.
#   2. os.environ — covers values injected at runtime (e.g. the AWS refresher writes
#      rotated STS creds straight into os.environ, not into Settings).
# Field/var names are matched by these substrings (case-insensitive):
_SECRET_NAME_HINTS = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_key", "session_token", "private_key", "auth_pass", "credential",
)
# Field names that contain a hint substring but are NOT secrets (avoid redacting
# their values — e.g. a token-refresh ENDPOINT URL is public infra, not a
# credential; redacting it would only make logs harder to debug).
_SECRET_NAME_DENY_SUFFIXES = ("_url", "_uri", "_endpoint", "_host", "_provider", "_region")
# Only redact values long enough that they can't be ordinary words/fragments.
_MIN_KNOWN_SECRET_LEN = 6
# Module-level cache of (literal, replacement); rebuilt by refresh_known_secrets().
_known_secret_literals: list[tuple[str, str]] = []


def _name_is_secret(name: str) -> bool:
    low = (name or "").lower()
    if any(low.endswith(suf) for suf in _SECRET_NAME_DENY_SUFFIXES):
        return False  # endpoints/URLs/hosts/regions are not credentials
    return any(h in low for h in _SECRET_NAME_HINTS)


def refresh_known_secrets() -> None:
    """Reload the known-secret literals from Settings + the environment.

    Called at startup and whenever credentials rotate (e.g. the AWS refresher
    swaps STS keys). Cheap; idempotent. Best-effort — never raises.
    """
    import os

    candidates: list[str] = []

    # Source 1: pydantic Settings object (covers .env-configured secrets).
    try:
        from app.config import get_settings
        dumped = get_settings().model_dump()
        for name, val in dumped.items():
            if _name_is_secret(name) and isinstance(val, str):
                candidates.append(val)
    except Exception:
        pass

    # Source 2: os.environ (covers runtime-injected creds, e.g. rotated STS).
    try:
        for name, val in os.environ.items():
            if _name_is_secret(name) and isinstance(val, str):
                candidates.append(val)
    except Exception:
        pass

    literals: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in candidates:
        val = (raw or "").strip()
        # Skip short values and obvious non-secret defaults/placeholders.
        if len(val) < _MIN_KNOWN_SECRET_LEN or val in seen:
            continue
        if val.lower() in ("change-me-in-production", "change-me-jwt-secret", "bedrock"):
            continue
        seen.add(val)
        literals.append((val, "[REDACTED_SECRET]"))
    # Longest-first so a value that is a substring of another is handled correctly.
    literals.sort(key=lambda t: len(t[0]), reverse=True)
    global _known_secret_literals
    _known_secret_literals = literals


# Populate once at import (best-effort; refreshed on rotation).
try:
    refresh_known_secrets()
except Exception:  # pragma: no cover - never block import on env read
    _known_secret_literals = []


def _redact_known_values(text: str) -> str:
    """Replace any known secret literal found in ``text`` (value-based)."""
    if not _known_secret_literals:
        return text
    result = text
    for literal, replacement in _known_secret_literals:
        if literal in result:
            result = result.replace(literal, replacement)
    return result


def redact_secrets(text: str) -> str:
    """Redact all detected secrets from text.

    Applies all known secret patterns and returns sanitized text.
    Safe to call on any string — returns original if no secrets found.

    Parameters
    ----------
    text:
        Text to scan and redact.

    Returns
    -------
    str:
        Text with all detected secrets replaced by ``[REDACTED_*]`` markers.
    """
    if not text:
        return text

    # Value-based first: catches arbitrary known credentials (e.g. low-entropy
    # passwords) regardless of length, that the regex patterns can't.
    result = _redact_known_values(text)

    if len(result) >= 10:
        for _name, pattern, replacement in _SECRET_PATTERNS:
            result = pattern.sub(replacement, result)

    return result


def redact_secrets_counted(text: str) -> tuple[str, int]:
    """Redact secrets and return count of redactions.

    Returns
    -------
    tuple:
        (redacted_text, number_of_redactions)
    """
    if not text:
        return text, 0

    result = text
    total = 0
    # Value-based first (length-independent).
    for literal, replacement in _known_secret_literals:
        if literal in result:
            count = result.count(literal)
            result = result.replace(literal, replacement)
            total += count

    if len(result) >= 10:
        for _name, pattern, replacement in _SECRET_PATTERNS:
            new_result, count = pattern.subn(replacement, result)
            total += count
            result = new_result

    return result, total


def is_secret_like(value: str) -> bool:
    """Quick heuristic: does this string look like a secret/token?

    Useful for pre-screening values before more expensive checks.

    Parameters
    ----------
    value:
        String to check.

    Returns
    -------
    bool:
        True if the value matches known secret patterns.
    """
    if not value or len(value) < 10:
        return False

    # Check known prefixes
    for prefix in _SECRET_PREFIXES:
        if value.startswith(prefix):
            return True

    # Check full patterns
    for _name, pattern, _repl in _SECRET_PATTERNS:
        if pattern.search(value):
            return True

    return False


class RedactingProcessor:
    """structlog processor that redacts secrets from all log event values.

    Usage in ``setup_logging()``::

        structlog.configure(
            processors=[
                ...,
                RedactingProcessor(),
                structlog.dev.ConsoleRenderer(),
            ]
        )

    This processor walks all values in the event dict and applies
    :func:`redact_secrets` to any string value. It runs BEFORE the
    renderer so secrets never reach log sinks.
    """

    def __call__(
        self,
        logger_: Any,
        method_name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        """Process a structlog event dict, redacting secrets from all string values."""
        return self._redact_dict(event_dict)

    def _redact_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact all string values in a dict."""
        for key, value in d.items():
            if isinstance(value, str) and len(value) >= 10:
                d[key] = redact_secrets(value)
            elif isinstance(value, dict):
                d[key] = self._redact_dict(value)
            elif isinstance(value, (list, tuple)):
                d[key] = self._redact_sequence(value)
        return d

    def _redact_sequence(self, seq: list | tuple) -> list | tuple:
        """Redact strings in a sequence."""
        result = []
        for item in seq:
            if isinstance(item, str) and len(item) >= 10:
                result.append(redact_secrets(item))
            elif isinstance(item, dict):
                result.append(self._redact_dict(item))
            else:
                result.append(item)
        return type(seq)(result) if isinstance(seq, tuple) else result
