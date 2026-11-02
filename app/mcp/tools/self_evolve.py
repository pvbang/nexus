"""
Nexus - Self-Evolve Toolchain.

These tools help the system "grow" by scaffolding new tools/skills on disk
and loading them at runtime.

Core idea:
- Agents can generate a new tool module under app/mcp/tools/custom/
- Import it so it self-registers into ToolRegistry
- Immediately use it (new schemas become available once the LLM receives the
  refreshed tool definitions)
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CUSTOM_TOOLS_DIR = _PROJECT_ROOT / "app" / "mcp" / "tools" / "custom"
_SKILLS_DIR = _PROJECT_ROOT / "skills"


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_ident(name: str, *, label: str) -> str | None:
    n = (name or "").strip()
    if not n:
        return f"{label} is required"
    if not _IDENT_RE.match(n):
        return f"{label} must be a valid Python identifier (letters/digits/_; not starting with digit)"
    return None


def _format_schema(description: str, parameters: dict[str, Any]) -> str:
    # Keep generated code stable and readable.
    import json
    return json.dumps(
        {
            "description": description,
            "parameters": parameters,
        },
        indent=4,
        ensure_ascii=False,
    )


async def _scaffold_custom_tool_module(params: dict[str, Any], **kwargs: Any) -> str:
    """
    Create a new custom tool module file and optionally import it to register the tool.
    """
    module_name = (params.get("module_name") or "").strip()
    tool_name = (params.get("tool_name") or "").strip()
    description = (params.get("description") or "").strip() or f"Custom tool: {tool_name}"
    toolset = (params.get("toolset") or "custom").strip()
    category = (params.get("category") or "custom").strip()
    emoji = (params.get("emoji") or "*").strip() or "*"
    overwrite = bool(params.get("overwrite", False))
    load_now = bool(params.get("load_now", True))

    parameters = params.get("parameters") or {"type": "object", "properties": {}}
    if not isinstance(parameters, dict):
        return tool_error("parameters must be an object (OpenAI tool 'parameters' schema).")

    err = _validate_ident(module_name, label="module_name") or _validate_ident(tool_name, label="tool_name")
    if err:
        return tool_error(err)

    _CUSTOM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    path = _CUSTOM_TOOLS_DIR / f"{module_name}.py"

    if path.exists() and not overwrite:
        return tool_error(f"File already exists: {path}. Set overwrite=true to replace.")

    schema_json = _format_schema(description, parameters)

    handler_fn = f"_{tool_name}"

    # Generate working implementation based on parameter types
    param_extractions = []
    param_validations = []
    output_parts = []
    for pname, pdef in (parameters or {}).items():
        ptype = pdef.get("type", "string")
        required = pdef.get("required", False)
        param_extractions.append(f'    {pname} = params.get("{pname}")')
        if required:
            param_validations.append(
                f'    if not {pname}:\n        return tool_error("{pname} is required")'
            )
        output_parts.append(f'"{pname}": {pname}')

    extract_block = "\n".join(param_extractions) if param_extractions else "    pass"
    validate_block = "\n".join(param_validations) if param_validations else ""
    output_dict = ", ".join(output_parts) if output_parts else ""

    content = f'''"""
Auto-generated custom tool module.

Module: app.mcp.tools.custom.{module_name}
Tool: {tool_name}
Description: {description}
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


async def {handler_fn}(params: dict[str, Any], **kwargs: Any) -> str:
    """Execute {tool_name}: {description}"""
{extract_block}
{validate_block}

    try:
        result_data = {{{output_dict}}}
        return tool_result(
            success=True,
            output=f"Executed {tool_name} successfully",
            data=result_data,
        )
    except Exception as e:
        logger.error("{tool_name}_failed", error=str(e)[:200])
        return tool_error(f"{tool_name} failed: {{e}}")


registry.register(
    name="{tool_name}",
    toolset="{toolset}",
    schema={schema_json},
    handler={handler_fn},
    is_async=True,
    emoji="{emoji}",
    category="{category}",
)
'''

    try:
        path.write_text(content, encoding="utf-8")
    except Exception as e:
        return tool_error(f"Failed to write custom tool module: {e}")

    loaded_tools: list[str] = []
    if load_now:
        dotted = f"app.mcp.tools.custom.{module_name}"
        before = set(registry.get_all_tool_names())
        try:
            importlib.invalidate_caches()
            importlib.import_module(dotted)
        except Exception as e:
            logger.error("custom_tool_import_failed", module=dotted, error=str(e)[:200])
            return tool_error(
                f"Created {path} but failed to import '{dotted}': {e}. "
                "Fix the module then call load_tool_module(module=...)"
            )
        # Ensure schemas are regenerated even if tool count didn't change (overwrite/reload).
        try:
            from app.mcp.tool_executor import ToolExecutor
            ToolExecutor.invalidate_schema_cache()
        except Exception:
            pass
        after = set(registry.get_all_tool_names())
        loaded_tools = sorted(after - before)

    return tool_result(
        success=True,
        output=(
            f"Custom tool module created: {path}\n"
            f"Module: app.mcp.tools.custom.{module_name}\n"
            + (f"New tools registered: {', '.join(loaded_tools)}" if load_now else "Not imported (load_now=false).")
        ),
        data={
            "module_name": module_name,
            "tool_name": tool_name,
            "path": str(path),
            "loaded": bool(load_now),
            "new_tools": loaded_tools,
        },
    )


async def _scaffold_skill(params: dict[str, Any], **kwargs: Any) -> str:
    skill_id = (params.get("skill_id") or "").strip()
    name = (params.get("name") or "").strip() or skill_id
    description = (params.get("description") or "").strip()
    body = (params.get("body") or "").strip()
    overwrite = bool(params.get("overwrite", False))
    reload_now = bool(params.get("reload_now", True))

    if not skill_id:
        return tool_error("skill_id is required (directory name under ./skills).")

    # Keep skill_id filesystem-safe.
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$", skill_id):
        return tool_error("skill_id contains invalid characters. Use letters/digits/._- only.")

    skill_dir = _SKILLS_DIR / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_path = skill_dir / "SKILL.md"

    if skill_path.exists() and not overwrite:
        return tool_error(f"Skill already exists: {skill_path}. Set overwrite=true to replace.")

    frontmatter_lines = [
        "---",
        f"name: {name}",
    ]
    if description:
        frontmatter_lines.append(f"description: {description}")
    frontmatter_lines.append("---")

    content = "\n".join(frontmatter_lines) + "\n\n" + (body or "# Instructions\n\nTODO: write skill instructions.\n")

    try:
        skill_path.write_text(content, encoding="utf-8")
    except Exception as e:
        return tool_error(f"Failed to write skill: {e}")

    if reload_now:
        try:
            from app.skills import get_skills_hub
            hub = get_skills_hub()
            hub.scan()
        except Exception as e:
            return tool_error(f"Skill written but failed to reload skills hub: {e}")

    return tool_result(
        success=True,
        output=f"Skill created: {skill_path}" + (" (skills reloaded)" if reload_now else ""),
        data={"skill_id": skill_id, "path": str(skill_path), "reloaded": reload_now},
    )


async def _reload_skills(params: dict[str, Any], **kwargs: Any) -> str:
    try:
        from app.skills import get_skills_hub
        hub = get_skills_hub()
        skills = hub.scan()
        return tool_result(
            success=True,
            output=f"Skills reloaded. Count={len(skills)}",
            data={"count": len(skills), "skills": sorted(skills.keys())[:200]},
        )
    except Exception as e:
        return tool_error(f"Failed to reload skills: {e}")


registry.register(
    name="scaffold_custom_tool_module",
    toolset="dev",
    schema={
        "description": (
            "Scaffold a new tool module under app/mcp/tools/custom/ and optionally import it so it registers immediately."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "module_name": {"type": "string", "description": "Python module name under app.mcp.tools.custom (no .py)"},
                "tool_name": {"type": "string", "description": "Tool name to register in ToolRegistry"},
                "description": {"type": "string", "description": "Tool description"},
                "parameters": {"type": "object", "description": "OpenAI tool 'parameters' schema (type=object, properties, required)."},
                "toolset": {"type": "string", "description": "Toolset name (default custom)"},
                "category": {"type": "string", "description": "Tool category (default custom)"},
                "emoji": {"type": "string", "description": "Emoji/marker for UI (default '*')"},
                "overwrite": {"type": "boolean", "default": False, "description": "Overwrite existing module file"},
                "load_now": {"type": "boolean", "default": True, "description": "Import the module immediately"},
            },
            "required": ["module_name", "tool_name"],
        },
    },
    handler=_scaffold_custom_tool_module,
    is_async=True,
    emoji="*",
    category="dev",
)


registry.register(
    name="scaffold_skill",
    toolset="dev",
    schema={
        "description": "Create a new skill under ./skills/<skill_id>/SKILL.md (with YAML frontmatter) and optionally reload the skills hub.",
        "parameters": {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "Directory name under ./skills"},
                "name": {"type": "string", "description": "Human-readable skill name (frontmatter)"},
                "description": {"type": "string", "description": "Short description (frontmatter)"},
                "body": {"type": "string", "description": "Markdown body for SKILL.md"},
                "overwrite": {"type": "boolean", "default": False, "description": "Overwrite existing SKILL.md"},
                "reload_now": {"type": "boolean", "default": True, "description": "Reload skills hub after writing"},
            },
            "required": ["skill_id"],
        },
    },
    handler=_scaffold_skill,
    is_async=True,
    emoji="*",
    category="dev",
)


registry.register(
    name="reload_skills",
    toolset="dev",
    schema={
        "description": "Rescan skills on disk and refresh the SkillsHub registry.",
        "parameters": {"type": "object", "properties": {}},
    },
    handler=_reload_skills,
    is_async=True,
    emoji="*",
    category="dev",
)


async def _discover_mcp_capability(params: dict[str, Any], **kwargs: Any) -> str:
    """Find and connect an external MCP server that provides a needed capability.

    The self-discovery loop (audit #1): when no existing tool covers what you need,
    describe the capability in plain language and the system matches it against a
    curated MCP-server catalog, connects the best fit, and bridges its tools into
    the registry so they're immediately callable as mcp__<server>__<tool>.
    """
    need = (params.get("need") or "").strip()
    if not need:
        return tool_error("'need' is required — describe the capability in plain language.")
    auto_connect = params.get("auto_connect", True)
    try:
        from app.mcp.acquire import acquire_capability
        from app.mcp.client import get_mcp_client_manager

        result = await acquire_capability(
            need,
            get_mcp_client_manager(),
            registry,
            auto_connect=bool(auto_connect),
        )
        ok = result.get("status") in ("connected", "proposed")
        return tool_result(success=ok, output=f"MCP discovery: {result.get('status')}", data=result)
    except Exception as e:
        return tool_error(f"MCP discovery failed: {e}")


registry.register(
    name="discover_mcp_capability",
    toolset="dev",
    schema={
        "description": (
            "Find and connect an external MCP server when you need a capability no current "
            "tool provides. Describe the need in plain language (e.g. 'query a postgres database', "
            "'automate a web browser'). On success, new tools appear as mcp__<server>__<tool>."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "need": {
                    "type": "string",
                    "description": "Plain-language description of the capability you need.",
                },
                "auto_connect": {
                    "type": "boolean",
                    "default": True,
                    "description": "If false, only propose a server instead of connecting.",
                },
            },
            "required": ["need"],
        },
    },
    handler=_discover_mcp_capability,
    is_async=True,
    emoji="🔌",
    category="dev",
)
