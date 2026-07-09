"""
MekongAI Nexus - Structured Logging (Hermes-agent inspired).

Centralized logging with:
- Session context injection (session_id on every log line)
- Component-based filtering (agent, swarm, tools, gateway)
- Rotating file handlers for agent.log and errors.log
- Comprehensive secret redaction via RedactingProcessor
- Noisy third-party logger suppression

Inspired by: hermes-agent/hermes_logging.py
Enhanced: Integrated app.core.secret_redaction for 20+ secret patterns
"""

import contextvars
import logging
import os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional, Sequence

import structlog

# Context-variable storage for per-session context (BUG-3 fix:
# threading.local doesn't isolate async tasks on the same thread;
# contextvars correctly isolates per-coroutine in async FastAPI)
_session_context_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "session_id", default=None
)

# Sentinel for initialization
_logging_initialized = False

# Noisy third-party loggers
_NOISY_LOGGERS = (
    "openai", "openai._base_client", "httpx", "httpcore",
    "asyncio", "hpack", "urllib3", "websockets", "botocore",
    "aiobotocore", "boto3", "charset_normalizer", "uvicorn.access",
    # HuggingFace / sentence-transformers ecosystem (extremely noisy filelock)
    "filelock", "transformers", "sentence_transformers",
    "huggingface_hub", "huggingface_hub.file_download",
)

# Component prefixes for filtering
COMPONENT_PREFIXES = {
    "agent": ("app.services.agent_spawner", "app.swarm.engine"),
    "tools": ("app.mcp.tool_executor", "app.mcp.tool_registry"),
    "swarm": ("app.swarm",),
    "xskill": ("app.xskill",),
    "api": ("app.api",),
    "gateway": ("app.gateway",),
    "cron": ("app.cron",),
    "memory": ("app.memory",),
    "orchestrator": ("app.services.orchestrator",),
}

# Secret patterns to redact from logs
_SECRET_PATTERNS = [
    (re.compile(r'(sk-[a-zA-Z0-9]{20,})'), r'sk-***REDACTED***'),
    (re.compile(r'(AKIA[A-Z0-9]{16})'), r'***AWS_KEY***'),
    (re.compile(r'(Bearer\s+[a-zA-Z0-9._\-]{20,})'), r'Bearer ***REDACTED***'),
    (re.compile(r'(password["\s:=]+["\']?)([^"\'\s,}]{4,})'), r'\1***REDACTED***'),
    (re.compile(r'(api[_-]?key["\s:=]+["\']?)([^"\'\s,}]{8,})'), r'\1***REDACTED***'),
]


# ---------------------------------------------------------------------------
# Session context API
# ---------------------------------------------------------------------------

def set_session_context(session_id: str) -> None:
    """Set the session ID for the current async task / coroutine."""
    _session_context_var.set(session_id)


def clear_session_context() -> None:
    """Clear the session ID for the current async task / coroutine."""
    _session_context_var.set(None)


def get_session_context() -> Optional[str]:
    """Get the current session ID."""
    return _session_context_var.get()


# ---------------------------------------------------------------------------
# Secret redaction (enhanced with comprehensive patterns)
# ---------------------------------------------------------------------------

def redact_sensitive_text(text: str) -> str:
    """Redact secrets from text before logging/display.

    Uses both legacy patterns (for backward compat) AND the comprehensive
    secret_redaction module (20+ patterns for API keys, tokens, etc.).
    """
    # Apply legacy patterns first (fast, specific)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)

    # Apply comprehensive secret_redaction patterns
    try:
        from app.core.secret_redaction import redact_secrets
        text = redact_secrets(text)
    except ImportError:
        pass  # Graceful degradation if module not available

    return text


class RedactingFormatter(logging.Formatter):
    """Formatter that redacts secrets from log messages."""

    def format(self, record: logging.LogRecord) -> str:
        # Inject session tag (uses contextvars for async safety)
        sid = _session_context_var.get()
        record.session_tag = f" [{sid}]" if sid else ""

        result = super().format(record)
        return redact_sensitive_text(result)


# ---------------------------------------------------------------------------
# Component filter
# ---------------------------------------------------------------------------

class ComponentFilter(logging.Filter):
    """Only pass records whose logger name starts with given prefixes."""

    def __init__(self, prefixes: Sequence[str]) -> None:
        super().__init__()
        self._prefixes = tuple(prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(self._prefixes)


# ---------------------------------------------------------------------------
# Main setup
# ---------------------------------------------------------------------------

def setup_logging(
    *,
    debug: bool = False,
    log_dir: Optional[Path] = None,
    log_level: Optional[str] = None,
    max_size_mb: int = 5,
    backup_count: int = 3,
) -> Optional[Path]:
    """Configure the MekongAI Nexus logging subsystem.

    Sets up:
    - structlog for structured JSON logging
    - agent.log (INFO+) — main activity log
    - errors.log (WARNING+) — quick triage log
    - Console output (DEBUG if debug=True)
    - Third-party logger suppression

    Safe to call multiple times — second call is a no-op.
    """
    global _logging_initialized
    if _logging_initialized:
        return log_dir

    level_name = (log_level or ("DEBUG" if debug else "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    # Import comprehensive RedactingProcessor for structlog pipeline
    try:
        from app.core.secret_redaction import RedactingProcessor as _StructlogRedactor
        _structlog_redactor = _StructlogRedactor()
    except ImportError:
        _structlog_redactor = None

    # Build structlog processor chain
    _processors = [
        structlog.contextvars.merge_contextvars,
        _add_session_id,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    # Insert RedactingProcessor BEFORE the renderer (secrets never reach sinks)
    if _structlog_redactor is not None:
        _processors.append(_structlog_redactor)
    _processors.append(
        structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer()
    )

    # Configure structlog
    structlog.configure(
        processors=_processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging
    root = logging.getLogger()
    root.setLevel(level)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    fmt = "%(asctime)s %(levelname)s%(session_tag)s %(name)s: %(message)s"
    console.setFormatter(RedactingFormatter(fmt))
    root.addHandler(console)

    # File handlers (if log_dir provided)
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        max_bytes = max_size_mb * 1024 * 1024

        # agent.log
        agent_handler = RotatingFileHandler(
            str(log_dir / "agent.log"),
            maxBytes=max_bytes,
            backupCount=backup_count,
        )
        agent_handler.setLevel(level)
        agent_handler.setFormatter(RedactingFormatter(fmt))
        root.addHandler(agent_handler)

        # errors.log
        error_handler = RotatingFileHandler(
            str(log_dir / "errors.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=2,
        )
        error_handler.setLevel(logging.WARNING)
        error_handler.setFormatter(RedactingFormatter(fmt))
        root.addHandler(error_handler)

    # Suppress noisy loggers
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    _logging_initialized = True
    return log_dir


def _add_session_id(logger_instance: Any, method_name: str, event_dict: dict) -> dict:
    """Structlog processor to add session_id to event dict."""
    sid = _session_context_var.get()
    if sid:
        event_dict["session_id"] = sid
    return event_dict


# Type stub for structlog processor
from typing import Any
