"""
Nexus - Cross-Turn Action Registry.

FIX GAP-5: Prevent duplicate work across turns.

Problem: ContextSharing.register_action() only deconflicts within ONE turn.
Two consecutive turns can redo the same work (agent A scans port 80 in turn 3,
agent B scans port 80 again in turn 5 because the action registry was cleared).

Solution: Persistent action registry that survives across turns.
- Tracks completed actions + their outcomes
- Agents see "already done" list before executing
- Allows re-attempts only if prior attempt FAILED
- Injected into agent execution context as "DO NOT REPEAT" list
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
_MAX_REGISTRY_ENTRIES = 500  # Prevent unbounded growth


@dataclass
class CompletedAction:
    """Record of an action that was already performed."""

    action_key: str  # Normalized action identifier (e.g. "web_search:example.com")
    agent_role: str
    turn: int
    success: bool
    outcome_summary: str = ""  # Brief description of what happened
    timestamp: float = field(default_factory=time.time)
    attempt_count: int = 1


class CrossTurnRegistry:
    """Persistent registry of completed actions across turns.

    Prevents duplicate work by tracking what has been done.
    Agents check this before executing to avoid redundant operations.

    Semantics:
    - Successful actions: "already done, skip"
    - Failed actions (attempt < 3): "can retry with different approach"
    - Failed actions (attempt >= 3): "skip, won't work"
    """

    def __init__(self, task_id: str) -> None:
        from app.core import task_paths
        self._task_id = task_id
        self._context_dir = task_paths.context_dir(task_id)
        self._registry_path = self._context_dir / "ACTION_REGISTRY.jsonl"
        self._actions: dict[str, CompletedAction] = {}  # action_key → CompletedAction
        self._load_existing()

    def _load_existing(self) -> None:
        """Load registry from disk."""
        if not self._registry_path.exists():
            return
        try:
            with open(self._registry_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        action = CompletedAction(**data)
                        self._actions[action.action_key] = action
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("action_registry_load_failed", error=str(e)[:100])

    def register_completed(
        self,
        action_key: str,
        agent_role: str,
        turn: int,
        success: bool,
        outcome_summary: str = "",
    ) -> None:
        """Register a completed action."""
        normalized = self._normalize_key(action_key)
        existing = self._actions.get(normalized)

        if existing:
            existing.attempt_count += 1
            existing.success = success
            existing.outcome_summary = outcome_summary
            existing.turn = turn
            existing.agent_role = agent_role
        else:
            self._actions[normalized] = CompletedAction(
                action_key=normalized,
                agent_role=agent_role,
                turn=turn,
                success=success,
                outcome_summary=outcome_summary,
            )

        # Persist
        self._persist_entry(self._actions[normalized])

        # Evict oldest if over limit
        if len(self._actions) > _MAX_REGISTRY_ENTRIES:
            self._evict_oldest()

    def should_skip(self, action_key: str) -> tuple[bool, str]:
        """Check if an action should be skipped.

        Returns:
            (should_skip: bool, reason: str)
            - (True, "Already done successfully ...") → skip
            - (True, "Failed 3+ times ...") → skip
            - (False, "") → proceed
            - (False, "Previously failed, retry allowed") → proceed with caution
        """
        normalized = self._normalize_key(action_key)
        action = self._actions.get(normalized)

        if not action:
            return False, ""

        if action.success:
            return True, (
                f"Already done successfully in turn {action.turn} by {action.agent_role}. "
                f"Outcome: {action.outcome_summary[:200]}"
            )

        if action.attempt_count >= 3:
            return True, (
                f"Failed {action.attempt_count} times (last: turn {action.turn}). "
                f"Last outcome: {action.outcome_summary[:200]}"
            )

        return False, (
            f"Previously failed (attempt {action.attempt_count}, turn {action.turn}). "
            f"Retry allowed with different approach."
        )

    def build_context_for_agent(self, agent_role: str, max_chars: int = 3000) -> str:
        """Build "already done" context for an agent.

        Shows successful actions (don't repeat) and exhausted failures (don't try).
        """
        if not self._actions:
            return ""

        parts = ["## COMPLETED ACTIONS (DO NOT REPEAT these):\n"]
        current_len = len(parts[0])

        # Sort: successful first, then failed (most recent first)
        sorted_actions = sorted(
            self._actions.values(),
            key=lambda a: (not a.success, -a.turn),
        )

        for action in sorted_actions:
            if action.success:
                line = f"- ✓ T{action.turn} [{action.agent_role}]: {action.action_key}"
                if action.outcome_summary:
                    line += f" → {action.outcome_summary[:100]}"
            elif action.attempt_count >= 3:
                line = f"- ✗ [{action.action_key}]: Failed {action.attempt_count}x, skip"
            else:
                continue  # Don't show ongoing failures (retry allowed)

            line += "\n"
            if current_len + len(line) > max_chars:
                break
            parts.append(line)
            current_len += len(line)

        if len(parts) <= 1:
            return ""

        return "".join(parts)

    def get_stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        successful = sum(1 for a in self._actions.values() if a.success)
        failed = sum(1 for a in self._actions.values() if not a.success)
        return {
            "total_actions": len(self._actions),
            "successful": successful,
            "failed": failed,
            "exhausted": sum(1 for a in self._actions.values() if not a.success and a.attempt_count >= 3),
        }

    def _normalize_key(self, key: str) -> str:
        """Normalize action key for deduplication."""
        return key.lower().strip()[:300]

    def _persist_entry(self, action: CompletedAction) -> None:
        """Append entry to disk."""
        try:
            with open(self._registry_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(action), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("action_registry_persist_failed", error=str(e)[:100])

    def _evict_oldest(self) -> None:
        """Remove oldest entries to stay under limit."""
        sorted_keys = sorted(
            self._actions.keys(),
            key=lambda k: self._actions[k].timestamp,
        )
        while len(self._actions) > _MAX_REGISTRY_ENTRIES:
            oldest_key = sorted_keys.pop(0)
            del self._actions[oldest_key]
