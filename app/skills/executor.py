"""
Nexus - Skill Executor.

Apply skill context to agent execution — prepend system prompts,
auto-detect matching skills, and validate tool requirements.
"""

import re
from typing import Any, Optional

import structlog

from app.skills.hub import SkillsHubMarketplace, get_skills_marketplace
from app.skills.manifest import SkillManifest

logger = structlog.get_logger(__name__)


class SkillExecutor:
    """
    Apply skill context to agent message execution.

    Handles system prompt injection, skill auto-detection,
    and tool requirement validation.
    """

    def __init__(self, hub: SkillsHubMarketplace) -> None:
        self._hub = hub

    # ------------------------------------------------------------------
    # Apply skill to messages
    # ------------------------------------------------------------------

    def apply_skill(
        self,
        skill_id: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Prepend skill's system prompt to messages.

        Args:
            skill_id: The skill to apply.
            messages: List of chat messages (OpenAI format).

        Returns:
            Updated messages list with skill system prompt prepended.
        """
        manifest = self._hub.get(skill_id)
        if manifest is None:
            logger.warning("skill_not_found", skill_id=skill_id)
            return messages

        if not manifest.enabled:
            logger.warning("skill_disabled", skill_id=skill_id)
            return messages

        if not manifest.system_prompt:
            logger.debug("skill_no_system_prompt", skill_id=skill_id)
            return messages

        # Build skill system message
        skill_system_msg: dict[str, Any] = {
            "role": "system",
            "content": self._build_skill_prompt(manifest),
        }

        # Prepend to messages (after any existing system messages)
        result = list(messages)
        insert_idx = 0
        for i, msg in enumerate(result):
            if msg.get("role") == "system":
                insert_idx = i + 1
            else:
                break

        result.insert(insert_idx, skill_system_msg)

        logger.info("skill_applied", skill_id=skill_id, messages_count=len(result))
        return result

    def _build_skill_prompt(self, manifest: SkillManifest) -> str:
        """Build the full skill system prompt with metadata context."""
        parts: list[str] = [
            f"[SKILL: {manifest.name} v{manifest.version}]",
            "",
            manifest.system_prompt,
        ]

        if manifest.examples:
            parts.append("")
            parts.append("[Skill Examples]")
            for i, example in enumerate(manifest.examples, 1):
                parts.append(f"Example {i}:")
                if "input" in example:
                    parts.append(f"  Input: {example['input']}")
                if "output" in example:
                    parts.append(f"  Output: {example['output']}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Auto-detect matching skill
    # ------------------------------------------------------------------

    def find_matching_skill(
        self,
        user_message: str,
    ) -> Optional[SkillManifest]:
        """Auto-detect which skill matches user intent via keyword matching.

        Uses a simple scoring system based on:
        - Skill name match
        - Tag match
        - Description keyword match
        - Tools keyword match

        Args:
            user_message: The user's message text.

        Returns:
            Best matching SkillManifest, or None if no good match found.
        """
        if not user_message.strip():
            return None

        # Ensure skills are loaded
        skills = self._hub.discover() if not self._hub._cache else list(self._hub._cache.values())

        # Only consider enabled skills
        enabled_skills = [s for s in skills if s.enabled]
        if not enabled_skills:
            return None

        msg_lower = user_message.lower()
        msg_words = set(re.findall(r"[a-z0-9]+", msg_lower))

        best_score = 0.0
        best_skill: Optional[SkillManifest] = None

        for skill in enabled_skills:
            score = 0.0

            # Name-based matching
            name_words = set(re.findall(r"[a-z0-9]+", skill.name.lower()))
            name_overlap = len(msg_words & name_words)
            score += name_overlap * 3.0

            # Skill ID matching
            id_words = set(skill.skill_id.split("-"))
            id_overlap = len(msg_words & id_words)
            score += id_overlap * 2.5

            # Tag matching
            tag_words: set[str] = set()
            for tag in skill.tags:
                tag_words.update(re.findall(r"[a-z0-9]+", tag.lower()))
            tag_overlap = len(msg_words & tag_words)
            score += tag_overlap * 2.0

            # Description keyword matching
            desc_words = set(re.findall(r"[a-z0-9]+", skill.description.lower()))
            desc_overlap = len(msg_words & desc_words)
            score += desc_overlap * 1.0

            # Tools matching
            tools_words: set[str] = set()
            for tool in skill.tools_required:
                tools_words.update(re.findall(r"[a-z0-9]+", tool.lower()))
            tools_overlap = len(msg_words & tools_words)
            score += tools_overlap * 1.5

            if score > best_score:
                best_score = score
                best_skill = skill

        # Require minimum score threshold
        min_threshold = 2.0
        if best_score >= min_threshold and best_skill is not None:
            logger.info(
                "skill_auto_matched",
                skill_id=best_skill.skill_id,
                score=best_score,
            )
            return best_skill

        return None

    # ------------------------------------------------------------------
    # Tool validation
    # ------------------------------------------------------------------

    def get_skill_tools(self, skill_id: str) -> list[str]:
        """Return list of tools required by a skill.

        Args:
            skill_id: The skill to query.

        Returns:
            List of tool names, empty if skill not found.
        """
        manifest = self._hub.get(skill_id)
        if manifest is None:
            return []
        return list(manifest.tools_required)

    def validate_skill_tools(
        self,
        skill_id: str,
        available_tools: list[str],
    ) -> dict[str, Any]:
        """Check if all required tools for a skill are available.

        Args:
            skill_id: The skill to validate.
            available_tools: List of currently available tool names.

        Returns:
            Dict with validation results:
            - valid: bool
            - required: list of required tool names
            - available: list of available required tools
            - missing: list of missing required tools
        """
        manifest = self._hub.get(skill_id)
        if manifest is None:
            return {
                "valid": False,
                "error": f"Skill not found: {skill_id}",
                "required": [],
                "available": [],
                "missing": [],
            }

        required = set(manifest.tools_required)
        available_set = set(available_tools)
        available_required = required & available_set
        missing = required - available_set

        return {
            "valid": len(missing) == 0,
            "required": sorted(required),
            "available": sorted(available_required),
            "missing": sorted(missing),
        }


    # ------------------------------------------------------------------
    # Skills metadata for prompt injection (Roo-Code lazy-load pattern)
    # ------------------------------------------------------------------

    def get_available_skills_metadata(self) -> list[dict[str, str]]:
        """
        Get lightweight metadata for all enabled skills.

        Used by PromptBuilder to inject skill names + descriptions
        into the system prompt (mandatory_skill_check pattern).
        Full instructions are loaded on-demand via load_skill tool.

        Uses SkillsHub (app/skills/__init__.py) which tolerates all
        frontmatter formats (loose YAML parsing). This is the reliable
        source that works with ALL skill formats in the skills/ directory.

        Returns:
            List of dicts with: name, description
        """
        try:
            from app.skills import get_skills_hub
            hub = get_skills_hub()
            return [
                {
                    "name": info.name,
                    "description": info.description or f"Skill: {info.name}",
                }
                for info in hub._skills.values()
                if info.description  # Skip skills without description
            ]
        except Exception as exc:
            logger.warning("skills_metadata_failed", error=str(exc))
            return []

    async def load_skill_content(self, skill_name: str) -> Optional[str]:
        """
        Load full SKILL.md content by skill name (for load_skill tool).

        Args:
            skill_name: Name of the skill (case-insensitive match).

        Returns:
            Full skill instructions string, or None if not found.
        """
        skills = self._hub.discover() if not self._hub._cache else list(self._hub._cache.values())

        # Find by name (case-insensitive)
        for s in skills:
            if s.name.lower() == skill_name.lower():
                if s.system_prompt:
                    return self._build_skill_prompt(s)
                # Fall back to reading the SKILL.md file body
                skill_path = self._hub.skills_dir / s.skill_id / "SKILL.md"
                if skill_path.exists():
                    content = skill_path.read_text(encoding="utf-8")
                    # Strip frontmatter
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            return parts[2].strip()
                    return content.strip()
                break

        return None


# ------------------------------------------------------------------
# Module singleton
# ------------------------------------------------------------------

_executor: Optional[SkillExecutor] = None


def get_skill_executor() -> SkillExecutor:
    """Get or create the global SkillExecutor singleton."""
    global _executor
    if _executor is None:
        hub = get_skills_marketplace()
        _executor = SkillExecutor(hub)
    return _executor
