"""
MekongAI Nexus - Tool Catalog Section.

By default returns empty string since tools are passed via native function-calling
schemas. Only sandbox/security notes are included when relevant.
"""

from typing import Optional


# Sandbox notes for code execution tools
_SANDBOX_NOTES = (
    "⚠️ **Code Sandbox Notes:**\n"
    "- `execute_code` sandbox has NO internet. For HTTP/API calls, use `http_request`.\n"
    "- Sandbox HAS pre-installed: fpdf2, matplotlib, pandas, numpy, pillow, openpyxl.\n"
    "- Use `generate_word_document` for .docx (NOT execute_code — sandbox lacks python-docx)."
)


def get_tool_catalog_section(
    tool_names: Optional[list[str]] = None,
    *,
    include_tool_list: bool = False,
) -> str:
    """
    Get tool catalog section for system prompt.

    By default returns only sandbox/security notes (tools are passed via
    native function-calling schemas, so listing them in text is redundant).

    Args:
        tool_names: If provided, only consider these tools.
        include_tool_list: If True, generate full text catalog (legacy mode).

    Returns:
        Tool catalog section string (empty by default).
    """
    if include_tool_list:
        return _build_full_catalog(tool_names)

    # Default: only return sandbox notes if code tools are available
    if tool_names is None or any(
        t in ("execute_code", "execute_command") for t in (tool_names or [])
    ):
        return _SANDBOX_NOTES

    return ""


def _build_full_catalog(tool_names: Optional[list[str]] = None) -> str:
    """Legacy: build full text tool catalog (opt-in only)."""
    from app.mcp.tool_registry import registry

    try:
        import app.mcp.tools  # noqa: F401
    except ImportError:
        pass

    catalog = registry.get_tool_catalog()
    if not catalog:
        return ""

    # Category display metadata
    CATEGORY_DISPLAY: dict[str, dict[str, str]] = {
        "web": {"emoji": "🔍", "label": "Web & Search"},
        "http": {"emoji": "🌐", "label": "HTTP & API"},
        "browser": {"emoji": "🖥️", "label": "Browser Automation"},
        "filesystem": {"emoji": "📁", "label": "File System"},
        "code": {"emoji": "💻", "label": "Code & Terminal"},
        "data": {"emoji": "📊", "label": "Data & Output"},
        "intelligence": {"emoji": "🧠", "label": "Agent Intelligence"},
        "utility": {"emoji": "🔧", "label": "Utility"},
    }
    CATEGORY_MERGE: dict[str, str] = {
        "terminal": "code",
        "search_patch": "filesystem",
        "http_request": "http",
        "todo": "intelligence",
        "session_search": "intelligence",
        "memory": "intelligence",
        "delegate": "intelligence",
        "memory_tool": "intelligence",
        "agent_autonomy": "intelligence",
    }

    merged: dict[str, list[dict[str, str]]] = {}
    for cat, tools in sorted(catalog.items()):
        display_cat = CATEGORY_MERGE.get(cat, cat)
        if display_cat not in merged:
            merged[display_cat] = []
        merged[display_cat].extend(tools)

    if tool_names is not None:
        tool_set = set(tool_names)
        merged = {
            cat: [t for t in tools if t["name"] in tool_set]
            for cat, tools in merged.items()
        }
        merged = {cat: tools for cat, tools in merged.items() if tools}

    lines = ["## YOUR TOOLS", ""]
    for cat in sorted(merged.keys()):
        tools = merged[cat]
        display = CATEGORY_DISPLAY.get(cat, {"emoji": "⚡", "label": cat.title()})
        lines.append(f"{display['emoji']} **{display['label']}:**")
        for tool in sorted(tools, key=lambda t: t["name"]):
            desc = tool["description"]
            if len(desc) > 200:
                desc = desc[:197] + "..."
            lines.append(f"- `{tool['name']}` — {desc}")
        lines.append("")

    # Sandbox note
    has_code = any(
        t["name"] in ("execute_code", "execute_command")
        for tools in merged.values()
        for t in tools
    )
    if has_code:
        lines.append(_SANDBOX_NOTES)

    return "\n".join(lines)
