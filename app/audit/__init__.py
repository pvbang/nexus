"""
MekongAI Nexus - Session Audit Logger.

Inspired by Shannon's WorkflowLogger — crash-safe, append-only, human-readable
log file per session. Optimized for `tail -f` viewing.

Provides structured audit trail for:
- Phase transitions (planning, execution, synthesis)
- Tool calls with timing
- LLM calls with token/cost metrics
- Error events
- Session summary on completion

Each session gets its own log file in the audit directory.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

from app.core.interfaces import AuditWriter

logger = structlog.get_logger(__name__)


@dataclass
class SessionMetrics:
    """Accumulated metrics for a session."""

    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_ms: float = 0.0
    errors: int = 0
    phases_completed: list[str] = field(default_factory=list)


@dataclass
class SessionSummary:
    """Final session summary."""

    session_id: str
    status: str  # "completed" | "failed" | "cancelled"
    metrics: SessionMetrics
    started_at: str
    ended_at: str
    error: Optional[str] = None


class SessionAuditLogger(AuditWriter):
    """
    Append-only audit logger for a single session.

    Creates a human-readable log file that tracks all operations
    during a session. Designed for production debugging with `tail -f`.

    File format:
        [2025-01-15 14:32:01] [PHASE] Starting: planning
        [2025-01-15 14:32:05] [LLM] model=gpt-4o tokens_in=1200 tokens_out=450 cost=$0.02 (3.2s)
        [2025-01-15 14:32:06] [TOOL] web_search: query="fastapi best practices" (1.1s)
        [2025-01-15 14:32:07] [ERROR] TimeoutError: LLM call exceeded 30s limit
    """

    def __init__(
        self,
        session_id: str,
        audit_dir: str = "./workspace/audit",
        user_id: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.user_id = user_id
        self._metrics = SessionMetrics()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._log_path = Path(audit_dir) / f"{session_id}.log"
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Create log file and write header."""
        if self._initialized:
            return

        # Ensure directory exists
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

        header = (
            "=" * 80 + "\n"
            f"MekongAI Nexus - Session Audit Log\n"
            "=" * 80 + "\n"
            f"Session ID:  {self.session_id}\n"
            f"User ID:     {self.user_id or 'anonymous'}\n"
            f"Started:     {self._format_timestamp()}\n"
            "=" * 80 + "\n\n"
        )

        await self._write(header)
        self._initialized = True

    async def log_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Append a general audit event."""
        await self._ensure_initialized()
        details = " ".join(f"{k}={v}" for k, v in data.items()) if data else ""
        line = f"[{self._format_timestamp()}] [{event_type.upper()}] {details}\n"
        await self._write(line)

    async def log_phase(self, phase: str, event: str) -> None:
        """Log a phase transition (start/complete)."""
        await self._ensure_initialized()
        action = "Starting" if event == "start" else "Completed"
        line = f"[{self._format_timestamp()}] [PHASE] {action}: {phase}\n"

        # Add blank line before phase start for readability
        if event == "start":
            line = "\n" + line

        await self._write(line)

        if event == "complete":
            self._metrics.phases_completed.append(phase)

    async def log_tool_call(
        self,
        tool_name: str,
        params: dict[str, Any],
        result: Any,
        duration_ms: float,
    ) -> None:
        """Log a tool invocation with timing."""
        await self._ensure_initialized()

        self._metrics.total_tool_calls += 1
        self._metrics.total_duration_ms += duration_ms

        # Format params for human readability (truncate long values)
        param_str = self._format_params(tool_name, params)
        duration_str = f"({duration_ms / 1000:.1f}s)"

        # Determine success/failure
        success = not isinstance(result, Exception)
        status = "✓" if success else "✗"

        line = f"[{self._format_timestamp()}] [TOOL] {status} {tool_name}: {param_str} {duration_str}\n"
        await self._write(line)

    async def log_llm_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
        cost_usd: Optional[float] = None,
    ) -> None:
        """Log an LLM call with metrics."""
        await self._ensure_initialized()

        self._metrics.total_llm_calls += 1
        self._metrics.total_input_tokens += input_tokens
        self._metrics.total_output_tokens += output_tokens
        self._metrics.total_duration_ms += duration_ms
        if cost_usd:
            self._metrics.total_cost_usd += cost_usd

        cost_str = f" cost=${cost_usd:.4f}" if cost_usd else ""
        line = (
            f"[{self._format_timestamp()}] [LLM] model={model} "
            f"tokens_in={input_tokens} tokens_out={output_tokens}"
            f"{cost_str} ({duration_ms / 1000:.1f}s)\n"
        )
        await self._write(line)

    async def log_error(self, error: Exception, context: Optional[str] = None) -> None:
        """Log an error event."""
        await self._ensure_initialized()

        self._metrics.errors += 1

        error_name = type(error).__name__
        context_str = f" ({context})" if context else ""
        line = f"[{self._format_timestamp()}] [ERROR] {error_name}: {error}{context_str}\n"
        await self._write(line)

    async def log_agent_start(self, agent_name: str, task: str) -> None:
        """Log agent execution start."""
        await self._ensure_initialized()
        truncated_task = task[:100] + "..." if len(task) > 100 else task
        line = f"[{self._format_timestamp()}] [AGENT] Starting: {agent_name} — {truncated_task}\n"
        await self._write(line)

    async def log_agent_end(
        self, agent_name: str, success: bool, duration_ms: float
    ) -> None:
        """Log agent execution end."""
        await self._ensure_initialized()
        status = "Completed" if success else "Failed"
        line = (
            f"[{self._format_timestamp()}] [AGENT] {status}: {agent_name} "
            f"({duration_ms / 1000:.1f}s)\n"
        )
        await self._write(line)

    async def log_summary(self) -> SessionSummary:
        """Write session completion summary and return it."""
        await self._ensure_initialized()

        ended_at = datetime.now(timezone.utc).isoformat()
        status = "completed" if self._metrics.errors == 0 else "failed"

        summary = SessionSummary(
            session_id=self.session_id,
            status=status,
            metrics=self._metrics,
            started_at=self._started_at,
            ended_at=ended_at,
        )

        total_duration_s = self._metrics.total_duration_ms / 1000

        lines = [
            "",
            "=" * 80,
            f"Session {status.upper()}",
            "─" * 40,
            f"Session ID:     {self.session_id}",
            f"Status:         {status}",
            f"Duration:       {total_duration_s:.1f}s",
            f"LLM Calls:      {self._metrics.total_llm_calls}",
            f"Tool Calls:     {self._metrics.total_tool_calls}",
            f"Total Tokens:   {self._metrics.total_input_tokens + self._metrics.total_output_tokens:,}",
            f"Total Cost:     ${self._metrics.total_cost_usd:.4f}",
            f"Errors:         {self._metrics.errors}",
            f"Phases:         {', '.join(self._metrics.phases_completed) or 'none'}",
            "=" * 80,
            "",
        ]

        await self._write("\n".join(lines) + "\n")
        return summary

    @property
    def metrics(self) -> SessionMetrics:
        """Get current accumulated metrics."""
        return self._metrics

    @property
    def log_path(self) -> Path:
        """Get the path to the log file."""
        return self._log_path

    # --- Private Methods ---

    async def _ensure_initialized(self) -> None:
        """Lazy initialization."""
        if not self._initialized:
            await self.initialize()

    async def _write(self, content: str) -> None:
        """Append content to log file (crash-safe)."""
        async with self._lock:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
            except OSError as e:
                logger.error("audit_write_failed", path=str(self._log_path), error=str(e))

    def _format_timestamp(self) -> str:
        """Format timestamp for log line (UTC, human readable)."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _format_params(self, tool_name: str, params: dict[str, Any]) -> str:
        """Format tool parameters for human-readable display."""
        if not params:
            return ""

        # Tool-specific formatting for common tools
        if tool_name in ("web_search", "search_engines") and "query" in params:
            return f'query="{self._truncate(str(params["query"]), 60)}"'
        if tool_name in ("read_file", "write_file") and "path" in params:
            return f'path="{params["path"]}"'
        if tool_name == "execute_code" and "code" in params:
            return f"code=({len(str(params['code']))} chars)"
        if tool_name in ("http_request", "fetch_url") and "url" in params:
            return f'url="{self._truncate(str(params["url"]), 80)}"'
        if tool_name == "terminal" and "command" in params:
            return f'cmd="{self._truncate(str(params["command"]), 80)}"'

        # Default: show first meaningful param
        for key, val in params.items():
            if isinstance(val, str) and val:
                return f'{key}="{self._truncate(val, 60)}"'
            if isinstance(val, (int, float, bool)):
                return f"{key}={val}"

        return f"({len(params)} params)"

    @staticmethod
    def _truncate(s: str, max_len: int) -> str:
        """Truncate string with ellipsis."""
        if len(s) <= max_len:
            return s
        return s[: max_len - 3] + "..."


# --- Factory ---

_audit_loggers: dict[str, SessionAuditLogger] = {}


def get_or_create_audit_logger(
    session_id: str,
    audit_dir: str = "./workspace/audit",
    user_id: Optional[str] = None,
) -> SessionAuditLogger:
    """
    Get or create an audit logger for a session.

    Reuses existing loggers for the same session to maintain state.
    """
    if session_id not in _audit_loggers:
        _audit_loggers[session_id] = SessionAuditLogger(
            session_id=session_id,
            audit_dir=audit_dir,
            user_id=user_id,
        )
    return _audit_loggers[session_id]


def remove_audit_logger(session_id: str) -> None:
    """Remove audit logger after session completes."""
    _audit_loggers.pop(session_id, None)
