"""
MekongAI Nexus - Structured Action Logger.

JSONL-based audit trail for agent actions, tool calls, swarm rounds,
and system events.

Pattern từ MiroFish: structured JSONL logging per agent/swarm session.

Each action is appended as a single JSON line to:
  workspace/logs/{session_id}/actions.jsonl

Provides:
- Per-session timeline reconstruction
- Per-agent statistics
- Session summary / analytics
- Filtered search by action_type or agent
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# Default log directory
DEFAULT_LOG_DIR = Path("workspace/logs")


# ============================================================
# Data Classes
# ============================================================


@dataclass
class ActionEntry:
    """A single structured action in the audit trail."""

    session_id: str
    timestamp: str  # ISO 8601
    action_type: str  # tool_call, agent_response, swarm_round, user_input, system_event
    agent_name: Optional[str] = None
    content: str = ""  # Brief description
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        d = asdict(self)
        # Remove None values for compact JSONL
        return {k: v for k, v in d.items() if v is not None}


# ============================================================
# Action Logger
# ============================================================


class ActionLogger:
    """
    JSONL-based structured action logger.

    Appends each action as a single JSON line to per-session log files.
    Thread-safe via append-only file writes.
    """

    def __init__(self, log_dir: Optional[Path] = None) -> None:
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Core logging
    # ------------------------------------------------------------------

    def log_action(self, action: ActionEntry) -> None:
        """
        Append an action entry as a JSON line to the session log file.

        File: {log_dir}/{session_id}/actions.jsonl
        """
        session_dir = self.log_dir / action.session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        log_file = session_dir / "actions.jsonl"
        line = json.dumps(action.to_dict(), ensure_ascii=False, default=str)

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def log_tool_call(
        self,
        session_id: str,
        agent_name: str,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        duration_ms: int = 0,
    ) -> None:
        """Log a tool call action."""
        self.log_action(ActionEntry(
            session_id=session_id,
            timestamp=_now_iso(),
            action_type="tool_call",
            agent_name=agent_name,
            content=f"Called tool '{tool_name}'",
            details={
                "tool_name": tool_name,
                "args": _safe_truncate(args),
                "result": _safe_truncate(result),
                "success": not isinstance(result, str) or "error" not in result.lower(),
            },
            duration_ms=duration_ms,
        ))

    def log_agent_response(
        self,
        session_id: str,
        agent_name: str,
        content: str,
        model: str = "",
        tokens_used: int = 0,
    ) -> None:
        """Log an agent response."""
        self.log_action(ActionEntry(
            session_id=session_id,
            timestamp=_now_iso(),
            action_type="agent_response",
            agent_name=agent_name,
            content=content[:200],
            details={
                "full_content_length": len(content),
                "model": model,
                "tokens_used": tokens_used,
            },
        ))

    def log_swarm_round(
        self,
        session_id: str,
        round_num: int,
        strategy: str,
        proposals: list[dict[str, Any]],
        outcome: Any,
    ) -> None:
        """Log a swarm debate/consensus round."""
        self.log_action(ActionEntry(
            session_id=session_id,
            timestamp=_now_iso(),
            action_type="swarm_round",
            agent_name=None,
            content=f"Swarm round {round_num} ({strategy})",
            details={
                "round_num": round_num,
                "strategy": strategy,
                "proposal_count": len(proposals),
                "proposals": [
                    {
                        "agent": p.get("agent", ""),
                        "content_preview": str(p.get("content", ""))[:200],
                        "tools_used": p.get("tools_used", []),
                    }
                    for p in proposals
                ],
                "outcome": _safe_truncate(outcome),
            },
        ))

    def log_user_input(
        self,
        session_id: str,
        content: str,
        user_id: str = "",
    ) -> None:
        """Log user input at the start of a request."""
        self.log_action(ActionEntry(
            session_id=session_id,
            timestamp=_now_iso(),
            action_type="user_input",
            agent_name=None,
            content=content[:200],
            details={
                "full_content_length": len(content),
                "user_id": user_id,
            },
        ))

    def log_system_event(
        self,
        session_id: str,
        event: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log a system event (e.g. task created, swarm formed)."""
        self.log_action(ActionEntry(
            session_id=session_id,
            timestamp=_now_iso(),
            action_type="system_event",
            agent_name=None,
            content=event,
            details=details or {},
        ))

    # ------------------------------------------------------------------
    # Query / Read
    # ------------------------------------------------------------------

    def get_session_timeline(self, session_id: str) -> list[ActionEntry]:
        """
        Read all actions for a session, sorted by timestamp.

        Args:
            session_id: Session identifier.

        Returns:
            List of ActionEntry sorted ascending by timestamp.
        """
        entries = self._read_session_entries(session_id)
        entries.sort(key=lambda e: e.timestamp)
        return entries

    def get_agent_stats(self, session_id: str) -> dict[str, Any]:
        """
        Aggregate statistics per agent for a session.

        Returns:
            Dict mapping agent_name → {total_actions, tool_calls, avg_response_time_ms}.
        """
        entries = self._read_session_entries(session_id)
        stats: dict[str, dict[str, Any]] = {}

        for entry in entries:
            agent = entry.agent_name or "__system__"
            if agent not in stats:
                stats[agent] = {
                    "total_actions": 0,
                    "tool_calls": 0,
                    "responses": 0,
                    "total_duration_ms": 0,
                    "duration_count": 0,
                }

            stats[agent]["total_actions"] += 1

            if entry.action_type == "tool_call":
                stats[agent]["tool_calls"] += 1

            if entry.action_type == "agent_response":
                stats[agent]["responses"] += 1

            if entry.duration_ms and entry.duration_ms > 0:
                stats[agent]["total_duration_ms"] += entry.duration_ms
                stats[agent]["duration_count"] += 1

        # Compute averages
        result: dict[str, Any] = {}
        for agent, s in stats.items():
            avg_ms = 0
            if s["duration_count"] > 0:
                avg_ms = s["total_duration_ms"] // s["duration_count"]
            result[agent] = {
                "total_actions": s["total_actions"],
                "tool_calls": s["tool_calls"],
                "responses": s["responses"],
                "avg_response_time_ms": avg_ms,
            }

        return result

    def get_session_summary(self, session_id: str) -> dict[str, Any]:
        """
        Generate overall session statistics.

        Returns:
            Dict with total_actions, total_tool_calls, agents_involved,
            action_types breakdown, duration, etc.
        """
        entries = self._read_session_entries(session_id)

        if not entries:
            return {
                "session_id": session_id,
                "total_actions": 0,
                "total_tool_calls": 0,
                "agents_involved": [],
                "action_types": {},
                "duration_ms": 0,
                "first_action": None,
                "last_action": None,
            }

        agents: set[str] = set()
        action_types: dict[str, int] = {}
        tool_calls = 0

        for entry in entries:
            if entry.agent_name:
                agents.add(entry.agent_name)

            action_types[entry.action_type] = action_types.get(entry.action_type, 0) + 1

            if entry.action_type == "tool_call":
                tool_calls += 1

        # Calculate session duration
        sorted_entries = sorted(entries, key=lambda e: e.timestamp)
        first_ts = sorted_entries[0].timestamp
        last_ts = sorted_entries[-1].timestamp
        duration_ms = _compute_duration_ms(first_ts, last_ts)

        return {
            "session_id": session_id,
            "total_actions": len(entries),
            "total_tool_calls": tool_calls,
            "agents_involved": sorted(agents),
            "action_types": action_types,
            "duration_ms": duration_ms,
            "first_action": first_ts,
            "last_action": last_ts,
        }

    def search_actions(
        self,
        session_id: str,
        action_type: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> list[ActionEntry]:
        """
        Filter actions by type or agent name.

        Args:
            session_id: Session identifier.
            action_type: Filter by action type (tool_call, agent_response, etc.).
            agent: Filter by agent name.

        Returns:
            Filtered list of ActionEntry.
        """
        entries = self._read_session_entries(session_id)

        if action_type:
            entries = [e for e in entries if e.action_type == action_type]

        if agent:
            entries = [e for e in entries if e.agent_name == agent]

        entries.sort(key=lambda e: e.timestamp)
        return entries

    def list_sessions(self) -> list[str]:
        """List all session IDs that have log files."""
        if not self.log_dir.exists():
            return []

        sessions: list[str] = []
        for entry in self.log_dir.iterdir():
            if entry.is_dir() and (entry / "actions.jsonl").exists():
                sessions.append(entry.name)

        return sorted(sessions)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_session_entries(self, session_id: str) -> list[ActionEntry]:
        """Read all JSONL entries for a session."""
        log_file = self.log_dir / session_id / "actions.jsonl"
        if not log_file.exists():
            return []

        entries: list[ActionEntry] = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        entries.append(ActionEntry(
                            session_id=data.get("session_id", session_id),
                            timestamp=data.get("timestamp", ""),
                            action_type=data.get("action_type", "unknown"),
                            agent_name=data.get("agent_name"),
                            content=data.get("content", ""),
                            details=data.get("details", {}),
                            duration_ms=data.get("duration_ms"),
                        ))
                    except json.JSONDecodeError:
                        logger.warning(
                            "action_log_parse_error",
                            session_id=session_id,
                            line_num=line_num,
                        )
        except OSError as e:
            logger.error("action_log_read_error", session_id=session_id, error=str(e))

        return entries


# ============================================================
# Helpers
# ============================================================


def _now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _safe_truncate(obj: Any, max_len: int = 500) -> Any:
    """Truncate large objects for logging."""
    if isinstance(obj, str):
        return obj[:max_len] if len(obj) > max_len else obj
    if isinstance(obj, dict):
        return {
            k: _safe_truncate(v, max_len)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_safe_truncate(item, max_len) for item in obj[:20]]
    return obj


def _compute_duration_ms(start_iso: str, end_iso: str) -> int:
    """Compute duration in ms between two ISO 8601 timestamps."""
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
        delta = (end - start).total_seconds() * 1000
        return max(0, int(delta))
    except (ValueError, TypeError):
        return 0


# ============================================================
# Module Singleton
# ============================================================

_action_logger: Optional[ActionLogger] = None


def get_action_logger() -> ActionLogger:
    """Get or create the module-level ActionLogger singleton."""
    global _action_logger
    if _action_logger is None:
        _action_logger = ActionLogger()
    return _action_logger
