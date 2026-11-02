"""
Nexus - User Profile (Hermes-agent USER.md pattern).

Persistent user preferences, coding style, language preferences.
Auto-loaded into system prompt for personalization.
Inspired by: hermes-agent USER.md + memory_nudge

Each user gets a USER.md file stored at:
  workspace_data/.user_profiles/{user_id}/USER.md
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

USER_PROFILES_BASE = Path("workspace_data/.user_profiles")
USER_PROFILES_BASE.mkdir(parents=True, exist_ok=True)

# Maximum lines loaded into system prompt (matches agent memory limit)
MAX_PROMPT_LINES = 200

_DEFAULT_TEMPLATE = """\
# User Profile

## Preferences
- Language: (auto-detected)
- Response style: detailed
- Code style: clean, well-commented

## Notes
<!-- Agent will append observations here -->
"""


class UserProfile:
    """
    File-based persistent user profile following the USER.md pattern.

    Similar to :class:`AgentMemoryFiles` but scoped to users rather than agents.
    The profile is auto-loaded into the system prompt so every agent
    interacting with the user can personalize its behaviour.
    """

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        self.base_dir = USER_PROFILES_BASE / user_id
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.profile_file = self.base_dir / "USER.md"
        self._ensure_default()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_default(self) -> None:
        """Create default USER.md on first access."""
        if not self.profile_file.exists():
            self.profile_file.write_text(_DEFAULT_TEMPLATE, encoding="utf-8")
            logger.info("user_profile_created", user_id=self.user_id)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> str:
        """Load USER.md content as a raw string."""
        self._ensure_default()
        return self.profile_file.read_text(encoding="utf-8")

    def save(self, content: str) -> None:
        """Overwrite USER.md with new content."""
        self.profile_file.write_text(content, encoding="utf-8")
        logger.debug("user_profile_saved", user_id=self.user_id)

    def append(self, note: str) -> None:
        """Append a timestamped note to USER.md."""
        existing = self.load()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n- [{timestamp}] {note}"
        self.profile_file.write_text(existing + entry, encoding="utf-8")
        logger.debug("user_profile_note_appended", user_id=self.user_id)

    def get_system_prompt_section(self) -> str:
        """Return a formatted section suitable for injection into a system prompt.

        Truncates at :data:`MAX_PROMPT_LINES` to avoid blowing up context.
        """
        content = self.load().strip()
        if not content:
            return ""

        lines = content.split("\n")
        if len(lines) > MAX_PROMPT_LINES:
            content = "\n".join(lines[:MAX_PROMPT_LINES])
            content += "\n\n[... truncated, profile too large ...]"

        return f"\n## User Profile\n{content}\n"

    def get_stats(self) -> dict[str, object]:
        """Return basic stats about the user profile."""
        file_size = self.profile_file.stat().st_size if self.profile_file.exists() else 0
        line_count = len(self.load().split("\n")) if self.profile_file.exists() else 0
        return {
            "user_id": self.user_id,
            "profile_file_size": file_size,
            "line_count": line_count,
        }


def get_user_profile(user_id: str) -> UserProfile:
    """Factory helper — returns a :class:`UserProfile` for the given user."""
    return UserProfile(user_id)
