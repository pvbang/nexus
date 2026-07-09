"""
Conversation Profiles — switchable personas per session.

Mỗi profile định nghĩa:
- name: tên profile (e.g. "coder", "analyst", "creative", "tutor")
- system_prompt_override: system prompt thay thế/bổ sung
- preferred_model: model ưa thích (optional)
- temperature: temperature override
- tools_whitelist: list tool names được phép (None = all)
- tools_blacklist: list tool names bị cấm
- reasoning_effort: default reasoning level
- max_tokens: response length override
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ConversationProfile:
    """Immutable profile definition for a conversation persona."""

    name: str
    description: str = ""
    system_prompt_override: str = ""
    preferred_model: Optional[str] = None
    temperature: Optional[float] = None
    tools_whitelist: Optional[list[str]] = None
    tools_blacklist: list[str] = field(default_factory=list)
    reasoning_effort: str = "medium"
    max_tokens: Optional[int] = None

    def to_dict(self) -> dict[str, object]:
        """Serialize profile to dict for API responses."""
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt_override": self.system_prompt_override,
            "preferred_model": self.preferred_model,
            "temperature": self.temperature,
            "tools_whitelist": self.tools_whitelist,
            "tools_blacklist": self.tools_blacklist,
            "reasoning_effort": self.reasoning_effort,
            "max_tokens": self.max_tokens,
        }


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

BUILTIN_PROFILES: list[ConversationProfile] = [
    ConversationProfile(
        name="default",
        description="Balanced general assistant — good for everyday tasks.",
        system_prompt_override=(
            "You are MekongAI Nexus, a balanced and helpful AI assistant. "
            "Provide clear, accurate answers. Use markdown for readability."
        ),
        temperature=0.7,
        reasoning_effort="medium",
    ),
    ConversationProfile(
        name="coder",
        description="Code-focused persona — precise, low temperature, dev tools enabled.",
        system_prompt_override=(
            "You are MekongAI Nexus in **Coder** mode. "
            "Focus on writing clean, efficient, well-documented code. "
            "Always include type hints. Prefer concise explanations with code examples. "
            "Use markdown code blocks with language tags."
        ),
        temperature=0.2,
        tools_whitelist=["terminal", "filesystem", "code_interpreter"],
        reasoning_effort="high",
        max_tokens=8192,
    ),
    ConversationProfile(
        name="analyst",
        description="Data analysis persona — structured, methodical, data tools enabled.",
        system_prompt_override=(
            "You are MekongAI Nexus in **Analyst** mode. "
            "Focus on data-driven insights. Structure your analysis with headers, "
            "tables, and bullet points. Cite numbers and sources. "
            "When uncertain, state confidence levels."
        ),
        temperature=0.3,
        tools_whitelist=["data", "web_search", "fetch_url", "code_interpreter"],
        reasoning_effort="high",
        max_tokens=6144,
    ),
    ConversationProfile(
        name="creative",
        description="Creative writing persona — high temperature, imaginative.",
        system_prompt_override=(
            "You are MekongAI Nexus in **Creative** mode. "
            "Be imaginative, expressive, and original. Use vivid language, "
            "metaphors, and storytelling techniques. Embrace unconventional ideas."
        ),
        temperature=0.95,
        tools_blacklist=["terminal", "filesystem"],
        reasoning_effort="low",
        max_tokens=4096,
    ),
    ConversationProfile(
        name="tutor",
        description="Educational persona — patient, step-by-step explanations.",
        system_prompt_override=(
            "You are MekongAI Nexus in **Tutor** mode. "
            "Explain concepts step-by-step, from simple to complex. "
            "Use analogies and examples. Ask clarifying questions when needed. "
            "Encourage the learner and check understanding."
        ),
        temperature=0.5,
        reasoning_effort="high",
        max_tokens=4096,
    ),
    ConversationProfile(
        name="researcher",
        description="Deep research persona — thorough, source-citing, web tools enabled.",
        system_prompt_override=(
            "You are MekongAI Nexus in **Researcher** mode. "
            "Conduct thorough research. Cite sources with URLs. "
            "Cross-reference multiple sources. Present balanced perspectives. "
            "Organize findings with clear structure and evidence hierarchy."
        ),
        temperature=0.3,
        tools_whitelist=["web_search", "fetch_url", "browser", "data"],
        reasoning_effort="xhigh",
        max_tokens=8192,
    ),
]


class ProfileManager:
    """
    Manages conversation profiles and session-profile assignments.

    Thread-safe via asyncio.Lock for mutable state.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, ConversationProfile] = {
            p.name: p for p in BUILTIN_PROFILES
        }
        self._session_profiles: dict[str, str] = {}  # session_id -> profile name
        self._lock = asyncio.Lock()

    # ------ Profile CRUD ------

    def get_profile(self, name: str) -> Optional[ConversationProfile]:
        """Return a profile by name, or None if not found."""
        return self._profiles.get(name)

    def list_profiles(self) -> list[ConversationProfile]:
        """Return all registered profiles."""
        return list(self._profiles.values())

    async def register_profile(self, profile: ConversationProfile) -> None:
        """Register a custom profile (overwrites if name exists)."""
        async with self._lock:
            self._profiles[profile.name] = profile
            logger.info("profile_registered", name=profile.name)

    # ------ Session mapping ------

    async def apply_to_session(self, session_id: str, profile_name: str) -> dict[str, object]:
        """
        Apply a profile to a session.

        Returns the applied settings dict, or raises ValueError if profile not found.
        """
        profile = self.get_profile(profile_name)
        if profile is None:
            raise ValueError(
                f"Profile '{profile_name}' not found. "
                f"Available: {', '.join(self._profiles.keys())}"
            )

        async with self._lock:
            self._session_profiles[session_id] = profile_name

        logger.info(
            "profile_applied",
            session_id=session_id,
            profile=profile_name,
        )
        return {
            "session_id": session_id,
            "profile": profile_name,
            "settings": profile.to_dict(),
        }

    def get_session_profile(self, session_id: str) -> Optional[str]:
        """Return the profile name for a session, or None."""
        return self._session_profiles.get(session_id)

    def get_session_profile_object(self, session_id: str) -> Optional[ConversationProfile]:
        """Return the full profile object for a session, or None."""
        name = self._session_profiles.get(session_id)
        if name is None:
            return None
        return self._profiles.get(name)

    def parse_profile_command(self, message: str) -> Optional[str]:
        """
        Parse /profile command from user message.

        - "/profile coder"  -> "coder"
        - "/profile"        -> "" (means show current)
        - non-command       -> None
        """
        stripped = message.strip()
        if not stripped.startswith("/profile"):
            return None

        parts = stripped.split(maxsplit=1)
        if len(parts) == 1:
            return ""  # show current
        return parts[1].strip().lower()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_profile_manager: Optional[ProfileManager] = None


def get_profile_manager() -> ProfileManager:
    """Return the global ProfileManager singleton."""
    global _profile_manager  # noqa: PLW0603
    if _profile_manager is None:
        _profile_manager = ProfileManager()
    return _profile_manager
