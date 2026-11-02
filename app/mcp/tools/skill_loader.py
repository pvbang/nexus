"""
Nexus - Skill Loading Tool (Roo-Code Lazy-Load Pattern).

Allows agents to load skill instructions on-demand after performing
the mandatory skill applicability check. Only the selected skill's
full instructions are loaded, saving tokens.

Self-registers with the tool registry at import time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)


async def _handle_load_skill(args: dict) -> str:
    """Load and return skill instructions by name."""
    skill_name = (args.get("skill_name") or "").strip()

    if not skill_name:
        return tool_error("skill_name is required")

    content = await _load_skill_content(skill_name)

    if content is None:
        available = _list_available_skills()
        hint = ", ".join(available[:15]) if available else "none found"
        # A missing skill is NOT a dead-end: the project can self-evolve. Point the
        # agent at research → scaffold_skill so the domain know-how gets built (and
        # captured for next time) instead of proceeding without it — which is how
        # domain errors (e.g. an implausible sizing) slip through (audit 2026-07-01).
        return tool_error(
            f"Skill '{skill_name}' not found. Available: {hint}.\n"
            f"If '{skill_name}' names a domain you need real know-how for, do NOT "
            f"proceed on guesswork: research it first (web_search / web_search_academic "
            f"for authoritative formulas/standards), then capture it with "
            f"scaffold_skill(skill_id=\"{skill_name}\", ...) so it loads now and is "
            f"reusable next time. Otherwise pick the closest skill from the list above."
        )

    logger.info("skill_loaded", skill_name=skill_name, content_length=len(content))

    return tool_result(
        success=True,
        output=(
            f"[SKILL LOADED: {skill_name}]\n\n"
            f"{content}\n\n"
            "[Follow these instructions precisely for the current request. "
            "Do NOT deviate from the skill-defined flow.]"
        ),
        data={"skill_name": skill_name, "content_length": len(content)},
    )


async def _load_skill_content(skill_name: str) -> Optional[str]:
    """
    Load full SKILL.md content by name.

    Searches in order:
    1. Working SkillsHub (app/skills/__init__.py)
    2. Project skills: ./skills/{name}/SKILL.md
    3. SkillExecutor fallback
    """
    # Try the working SkillsHub first
    try:
        from app.skills import get_skills_hub
        hub = get_skills_hub()
        skill = hub.get_skill(skill_name)
        if skill and skill.content:
            return skill.content.strip()

        # Try case-insensitive match
        for name, info in hub._skills.items():
            if name.lower() == skill_name.lower():
                if info.content:
                    return info.content.strip()
    except Exception:
        pass

    # Try direct filesystem lookup
    _project_root = Path(__file__).resolve().parent.parent.parent.parent
    project_skills_dir = _project_root / "skills"
    if project_skills_dir.exists():
        skill_path = project_skills_dir / skill_name / "SKILL.md"
        if skill_path.exists():
            return _parse_skill_file(skill_path)

        # Case-insensitive match
        for d in project_skills_dir.iterdir():
            if d.is_dir() and d.name.lower() == skill_name.lower():
                candidate = d / "SKILL.md"
                if candidate.exists():
                    return _parse_skill_file(candidate)

    # Try SkillExecutor integration (legacy)
    try:
        from app.skills.executor import get_skill_executor
        executor = get_skill_executor()
        if hasattr(executor, "load_skill_content"):
            content = await executor.load_skill_content(skill_name)
            if content:
                return content
    except Exception:
        pass

    return None


def _parse_skill_file(path: Path) -> str:
    """Parse SKILL.md file, stripping front matter if present."""
    content = path.read_text(encoding="utf-8")

    # Strip YAML front matter (--- ... ---)
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()

    return content.strip()


def _list_available_skills() -> list[str]:
    """List available skill names from the working SkillsHub."""
    try:
        from app.skills import get_skills_hub
        hub = get_skills_hub()
        return sorted(hub._skills.keys())
    except Exception:
        pass

    # Fallback: filesystem scan
    skills_dir = Path(__file__).resolve().parent.parent.parent.parent / "skills"
    if not skills_dir.exists():
        return []
    return sorted(
        d.name for d in skills_dir.iterdir()
        if d.is_dir() and (d / "SKILL.md").exists()
    )


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

registry.register(
    name="load_skill",
    toolset="intelligence",
    schema={
        "description": (
            "Load a skill's full instructions by name. Use this ONLY after the "
            "mandatory skill applicability check determines a skill applies to "
            "the user's request. Returns the skill's complete instructions which "
            "you must follow precisely."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": (
                        "Name of the skill to load (from <available_skills> "
                        "in your system prompt)"
                    ),
                },
            },
            "required": ["skill_name"],
        },
    },
    handler=_handle_load_skill,
    is_async=True,
    emoji="📚",
)
