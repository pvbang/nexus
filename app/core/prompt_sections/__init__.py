"""
MekongAI Nexus - Modular Prompt Sections (Roo-Code inspired).

Each section is an independent module that returns a string section
for the system prompt. Composed by PromptBuilder in deterministic order
for prompt cache stability.

Section ordering (static → dynamic for cache optimization):
1. Identity (static per agent type)
2. System Info (semi-static, changes per request)
3. Tool Catalog (semi-static, changes when tools change)
4. Behaviors (static per agent type)
5. Self-Solving + Objective (static, shared)
6. Capabilities (static, shared)
7. Response Format (static, shared)
8. Skills (dynamic, per-request)
"""

from .identity import get_identity_section
from .behaviors import get_behaviors_section
from .system_info import get_system_info_section
from .tool_catalog import get_tool_catalog_section
from .objective import get_objective_section
from .reasoning_doctrine import get_reasoning_doctrine_section
from .capabilities import get_capabilities_section
from .response_format import get_response_format_section
from .skills import get_skills_section
from .owner_context import get_owner_context_section

__all__ = [
    "get_identity_section",
    "get_behaviors_section",
    "get_system_info_section",
    "get_tool_catalog_section",
    "get_objective_section",
    "get_reasoning_doctrine_section",
    "get_capabilities_section",
    "get_response_format_section",
    "get_skills_section",
    "get_owner_context_section",
]
