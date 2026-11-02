"""
Nexus - Skills Section (Roo-Code Mandatory Skill Check pattern).

Generates the skills section with mandatory applicability check.
Skills are lazy-loaded: only metadata (name + description) is included
in the prompt. Full skill instructions loaded on-demand via tool.
"""

from typing import Optional


def get_skills_section(available_skills: Optional[list[dict]] = None) -> str:
    """
    Generate the skills section with mandatory applicability check.

    Inspired by Roo-Code: agents MUST check skill applicability before
    every response. Skills are lazy-loaded (only when agent selects one),
    saving significant tokens.

    Args:
        available_skills: List of skill metadata dicts with:
            - name: str
            - description: str
            - path: str (SKILL.md location)
            If None or empty, returns empty string.

    Returns:
        Skills section string for system prompt.
    """
    if not available_skills:
        return ""

    skills_xml = "\n".join(
        f'  <skill>\n'
        f'    <name>{_escape_xml(s["name"])}</name>\n'
        f'    <description>{_escape_xml(s["description"])}</description>\n'
        f'  </skill>'
        for s in available_skills
    )

    return f"""====

AVAILABLE SKILLS

<available_skills>
{skills_xml}
</available_skills>

<mandatory_skill_check>
REQUIRED PRECONDITION

Before producing ANY user-facing response, you MUST perform a skill applicability check.

Step 1: Skill Evaluation
- Evaluate the user's request against ALL available skill <description> entries in <available_skills>.
- Determine whether at least one skill clearly and unambiguously applies.

Step 2: Branching Decision

<if_skill_applies>
- Select EXACTLY ONE skill.
- Prefer the most specific skill when multiple skills match.
- Use the `load_skill` tool to load the skill by name.
- Load the skill's instructions fully into context BEFORE continuing.
- Follow the skill instructions precisely.
- Do NOT respond outside the skill-defined flow.
</if_skill_applies>

<if_no_skill_applies>
- Proceed with a normal response.
- Do NOT load any SKILL.md files.
</if_no_skill_applies>

CONSTRAINTS:
- Do NOT load every skill up front.
- Load skills ONLY after a skill is selected.
- Do NOT reload a skill whose instructions already appear in this conversation.
- Do NOT skip this check.
- FAILURE to perform this check is an error.
</mandatory_skill_check>

<linked_file_handling>
- When a skill is loaded, ONLY the skill instructions are present.
- Files linked from the skill are NOT loaded automatically.
- The model MUST explicitly decide to read a linked file based on task relevance.
- Do NOT assume the contents of linked files unless they have been explicitly read.
- Prefer reading the minimum necessary linked file.
- Treat linked files as progressive disclosure, not mandatory context.
</linked_file_handling>
"""


def _escape_xml(value: str) -> str:
    """Escape XML special characters."""
    return (
        value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
