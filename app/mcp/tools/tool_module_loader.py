"""
Nexus - Dynamic Tool Module Loader.

Allows loading (importing) additional tool modules at runtime so agents can:
1) Create a new tool module under app/mcp/tools/custom/
2) Import it to self-register into the tool registry
3) Immediately use it in the same task/session

Security note: this is restricted to app.mcp.tools.custom.* by default to avoid
importing arbitrary modules with side effects.
"""

from __future__ import annotations

import importlib
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


async def _load_tool_module(params: dict[str, Any], **kwargs: Any) -> str:
    module = (params.get("module") or "").strip()
    if not module:
        return tool_error("Parameter 'module' is required (e.g. app.mcp.tools.custom.my_tool).")

    allowed_prefixes = (
        "app.mcp.tools.custom.",
    )
    if not module.startswith(allowed_prefixes):
        return tool_error(
            "Dynamic tool loading is restricted. Module must start with: "
            + ", ".join(allowed_prefixes)
        )

    before = set(registry.get_all_tool_names())
    try:
        importlib.import_module(module)
    except Exception as e:
        logger.error("tool_module_load_failed", module=module, error=str(e)[:200])
        return tool_error(f"Failed to import module '{module}': {e}")

    # Dynamic tools may overwrite existing tool schemas without changing tool count.
    # Ensure function-calling schemas are regenerated.
    try:
        from app.mcp.tool_executor import ToolExecutor
        ToolExecutor.invalidate_schema_cache()
    except Exception:
        pass

    after = set(registry.get_all_tool_names())
    new_tools = sorted(after - before)

    logger.info("tool_module_loaded", module=module, new_tools=len(new_tools))
    return tool_result(
        success=True,
        output=(
            f"Imported {module}. "
            + (f"New tools registered: {', '.join(new_tools)}" if new_tools else "No new tools registered.")
        ),
        data={"module": module, "new_tools": new_tools},
    )


registry.register(
    name="load_tool_module",
    toolset="dev",
    schema={
        "description": "Import a tool module so it self-registers tools into the registry (restricted to app.mcp.tools.custom.*).",
        "parameters": {
            "type": "object",
            "properties": {
                "module": {"type": "string", "description": "Dotted module path, e.g. app.mcp.tools.custom.my_tool"},
            },
            "required": ["module"],
        },
    },
    handler=_load_tool_module,
    is_async=True,
    emoji="*",
    category="dev",
)
