"""
Reasoning Effort Control — dynamic control over LLM reasoning depth.

Levels:
- "off"     — no chain-of-thought, direct answers only
- "minimal" — 1-sentence reasoning
- "low"     — brief reasoning (2-3 sentences)
- "medium"  — standard reasoning (default)
- "high"    — detailed step-by-step reasoning
- "xhigh"  — exhaustive analysis, show all considerations
"""

from __future__ import annotations

import asyncio
import enum
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


class ReasoningLevel(str, enum.Enum):
    """Enumeration of supported reasoning effort levels."""

    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


# ---------------------------------------------------------------------------
# Prompt modifiers per level
# ---------------------------------------------------------------------------

_REASONING_PROMPTS: dict[ReasoningLevel, str] = {
    ReasoningLevel.OFF: (
        "Answer directly without explanation. Do not show reasoning or "
        "chain-of-thought. Be concise."
    ),
    ReasoningLevel.MINIMAL: (
        "Provide a very brief 1-sentence rationale before your answer, "
        "then give the answer."
    ),
    ReasoningLevel.LOW: (
        "Include brief reasoning (2-3 sentences) before your answer. "
        "Keep explanations short and focused."
    ),
    ReasoningLevel.MEDIUM: (
        "Provide clear reasoning before your answer. Explain your thought "
        "process at a moderate level of detail."
    ),
    ReasoningLevel.HIGH: (
        "Think step-by-step. Provide detailed reasoning before your answer. "
        "Break down complex problems into clear logical steps."
    ),
    ReasoningLevel.XHIGH: (
        "Think step-by-step. Consider all angles and edge cases. "
        "Show your complete reasoning process, including alternatives "
        "you considered and why you rejected them. Be exhaustive in "
        "your analysis."
    ),
}


class ReasoningController:
    """
    Controls per-session reasoning effort level.

    Thread-safe via asyncio.Lock for mutable state.
    """

    def __init__(self, default_level: str = "medium") -> None:
        try:
            self._default_level = ReasoningLevel(default_level)
        except ValueError:
            self._default_level = ReasoningLevel.MEDIUM

        self._session_levels: dict[str, ReasoningLevel] = {}
        self._lock = asyncio.Lock()

    # ------ Level management ------

    async def set_level(self, session_id: str, level: ReasoningLevel | str) -> None:
        """Set the reasoning level for a session."""
        if isinstance(level, str):
            level = ReasoningLevel(level)

        async with self._lock:
            self._session_levels[session_id] = level

        logger.info(
            "reasoning_level_set",
            session_id=session_id,
            level=level.value,
        )

    def get_level(self, session_id: str) -> ReasoningLevel:
        """Get the reasoning level for a session (falls back to default)."""
        return self._session_levels.get(session_id, self._default_level)

    # ------ Prompt building ------

    @staticmethod
    def build_reasoning_prompt(level: ReasoningLevel | str) -> str:
        """
        Build the reasoning prompt modifier for a given level.

        This string is prepended/appended to the system prompt.
        """
        if isinstance(level, str):
            try:
                level = ReasoningLevel(level)
            except ValueError:
                level = ReasoningLevel.MEDIUM

        return _REASONING_PROMPTS.get(level, _REASONING_PROMPTS[ReasoningLevel.MEDIUM])

    def get_session_reasoning_prompt(self, session_id: str) -> str:
        """Get the reasoning prompt modifier for a session."""
        level = self.get_level(session_id)
        return self.build_reasoning_prompt(level)

    # ------ Command parsing ------

    @staticmethod
    def parse_think_command(message: str) -> Optional[str]:
        """
        Parse /think command from user message.

        Returns:
        - level string (e.g. "high") if valid command with argument
        - "" if command without argument (show current)
        - None if message is not a /think command
        """
        stripped = message.strip()
        if not stripped.startswith("/think"):
            return None

        parts = stripped.split(maxsplit=1)
        if len(parts) == 1:
            return ""  # show current level

        arg = parts[1].strip().lower()

        # Validate the argument
        valid_levels = {level.value for level in ReasoningLevel}
        if arg in valid_levels:
            return arg

        # Friendly aliases
        aliases: dict[str, str] = {
            "none": "off",
            "disable": "off",
            "min": "minimal",
            "brief": "low",
            "normal": "medium",
            "default": "medium",
            "detailed": "high",
            "max": "xhigh",
            "maximum": "xhigh",
            "exhaustive": "xhigh",
        }
        return aliases.get(arg)

    @staticmethod
    def list_levels() -> list[dict[str, str]]:
        """Return all reasoning levels with descriptions."""
        descriptions: dict[ReasoningLevel, str] = {
            ReasoningLevel.OFF: "No reasoning — direct answers only",
            ReasoningLevel.MINIMAL: "1-sentence rationale",
            ReasoningLevel.LOW: "Brief reasoning (2-3 sentences)",
            ReasoningLevel.MEDIUM: "Standard reasoning (default)",
            ReasoningLevel.HIGH: "Detailed step-by-step reasoning",
            ReasoningLevel.XHIGH: "Exhaustive analysis, all considerations shown",
        }
        return [
            {"level": level.value, "description": descriptions[level]}
            for level in ReasoningLevel
        ]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_reasoning_controller: Optional[ReasoningController] = None


def get_reasoning_controller(default_level: str = "") -> ReasoningController:
    """Return the global ReasoningController singleton.

    Default reasoning level is read from config (AGENT_REASONING_EFFORT).
    Falls back to 'xhigh' if not configured.
    """
    global _reasoning_controller  # noqa: PLW0603
    if _reasoning_controller is None:
        if not default_level:
            try:
                from app.config import get_settings
                default_level = get_settings().effective_reasoning_effort
            except Exception:
                default_level = "xhigh"
        _reasoning_controller = ReasoningController(default_level=default_level)
    return _reasoning_controller
