"""
Nexus - Prompt Builder (Roo-Code + OpenClaw-inspired).

Modular prompt assembly for Core Agent and Sub-Agents.
Auto-generates tool catalog from registry metadata.
Deterministic ordering for prompt cache stability.

Architecture (Phase 1 Refactor):
- Sections extracted to app/core/prompt_sections/ package
- Each section is independently testable
- PromptBuilder is now a thin composer layer
- Static sections → cached by Anthropic
- Dynamic sections (datetime, history, skill) → appended last for cache stability
"""

from typing import Any, Optional

from app.core.environment_details import FILE_EDITING_RULES
from app.core.prompt_sections import (
    get_identity_section,
    get_behaviors_section,
    get_system_info_section,
    get_tool_catalog_section,
    get_objective_section,
    get_reasoning_doctrine_section,
    get_capabilities_section,
    get_response_format_section,
    get_skills_section,
    get_owner_context_section,
)


# ===========================================================================
# PromptBuilder class
# ===========================================================================


class PromptBuilder:
    """
    Modular prompt assembly — Roo-Code + OpenClaw inspired.

    Sections assembled in deterministic order for cache stability:
    1. Identity (static, per agent type)
    2. System Context (semi-static: date/time, OS)
    3. Tool Catalog (auto-generated from registry, sorted)
    4. File Editing Rules (static)
    5. Behavior Rules (per agent type)
    6. Self-Solving Strategy / Objective (shared)
    7. Capabilities (task docs, collaboration)
    8. Response Format (shared)
    9. Dynamic sections (skill, profile, reasoning) — appended last

    Each section is defined in app/core/prompt_sections/ and can be
    tested independently.
    """

    def build_system_prompt(
        self,
        agent_type: str = "core",
        tool_names: Optional[list[str]] = None,
        skill_context: str = "",
        conversation_profile: str = "",
        reasoning_modifier: str = "",
        include_system_context: bool = True,
        include_self_solving: bool = True,
        available_skills: Optional[list[dict]] = None,
    ) -> str:
        """Assemble complete system prompt from modular sections.

        Args:
            agent_type: Agent role key (core, researcher, developer, etc.)
            tool_names: Optional explicit tool names to include. If None,
                        includes all available tools from registry.
            skill_context: Legacy skill instructions to inject (from skill executor).
            conversation_profile: Profile override text.
            reasoning_modifier: Reasoning effort modifier text.
            include_system_context: Whether to include date/time/OS info.
            include_self_solving: Whether to include self-solving strategies.
            available_skills: Skill metadata for mandatory skill check section.

        Returns:
            Complete system prompt string.
        """
        sections: list[str] = []

        # 1. Identity (static per agent type — cacheable)
        sections.append(get_identity_section(agent_type))

        # 2. System context (semi-static — changes per request but small)
        if include_system_context:
            sections.append(get_system_info_section())

        # 3. Tool catalog (auto from registry — deterministic order)
        sections.append(get_tool_catalog_section(tool_names))

        # 4. File editing rules (appended after tool catalog)
        sections.append(FILE_EDITING_RULES)

        # 5. Behavior rules (per agent type)
        sections.append(get_behaviors_section(agent_type))

        # 6. Self-solving strategy / Objective (shared, for tool-using agents)
        if include_self_solving and agent_type != "judge":
            sections.append(get_objective_section())
            # Reasoning-First Doctrine: keeps search as an input to thinking, not a
            # substitute for it. Placed right after Objective so it frames HOW the
            # agent works before the per-task capabilities are listed.
            sections.append(get_reasoning_doctrine_section())

        # 7. Capabilities: Task docs + Agent autonomy (for tool-using agents)
        if include_self_solving and agent_type != "judge":
            sections.append(get_capabilities_section(agent_type))

        # 8. Response format (shared)
        sections.append(get_response_format_section())

        # 8.5. Owner context (wallet addresses, GitHub, payment info)
        sections.append(get_owner_context_section())

        # 9. Skills section (Roo-Code mandatory skill check pattern)
        if available_skills:
            sections.append(get_skills_section(available_skills))

        # 10. Dynamic sections (appended LAST for cache stability)
        if skill_context:
            sections.append(f"## ACTIVE SKILL\n{skill_context}")
        if conversation_profile:
            sections.append(f"## CONVERSATION PROFILE\n{conversation_profile}")
        if reasoning_modifier:
            sections.append(f"[Reasoning Mode]: {reasoning_modifier}")

        return "\n\n".join(s for s in sections if s)

    # ------------------------------------------------------------------
    # Convenience: build messages array
    # ------------------------------------------------------------------

    def build_messages(
        self,
        user_message: str,
        agent_type: str = "core",
        tool_names: Optional[list[str]] = None,
        history: Optional[list[dict[str, Any]]] = None,
        skill_context: str = "",
        conversation_profile: str = "",
        reasoning_modifier: str = "",
        available_skills: Optional[list[dict]] = None,
    ) -> list[dict[str, Any]]:
        """Build complete messages array with system prompt + history + user message.

        Convenience method that combines PromptBuilder.build_system_prompt()
        with history injection from conversation_context module.

        Args:
            user_message: Current user message.
            agent_type: Agent role key.
            tool_names: Optional tool filter.
            history: Prior conversation messages (already loaded).
            skill_context: Skill instructions.
            conversation_profile: Profile override.
            reasoning_modifier: Reasoning effort text.
            available_skills: Skill metadata for mandatory skill check.

        Returns:
            List of messages ready for LLM call.
        """
        system_prompt = self.build_system_prompt(
            agent_type=agent_type,
            tool_names=tool_names,
            skill_context=skill_context,
            conversation_profile=conversation_profile,
            reasoning_modifier=reasoning_modifier,
            available_skills=available_skills,
        )

        system_msg = {"role": "system", "content": system_prompt}
        user_msg = {"role": "user", "content": user_message}

        if history:
            # Use conversation_context module for proper injection
            try:
                from app.memory.conversation_context import inject_history_into_messages
                return inject_history_into_messages(
                    system_message=system_msg,
                    current_user_message=user_msg,
                    history=history,
                )
            except Exception:
                pass  # Fallback below

        return [system_msg, user_msg]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_builder: Optional[PromptBuilder] = None


def get_prompt_builder() -> PromptBuilder:
    """Get or create the global PromptBuilder singleton."""
    global _builder
    if _builder is None:
        _builder = PromptBuilder()
    return _builder
