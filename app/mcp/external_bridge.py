"""Bridge external MCP server tools into the internal ToolRegistry (audit #1).

The MCP client (``app/mcp/client.py``) connects to external MCP servers and can
list/call their tools — but agents only ever see tools from the internal
``ToolRegistry``. Without a bridge, auto-connecting MCP servers at startup would
do nothing useful: the tools would sit in ``MCPClientManager`` unreachable by any
agent.

This module registers each connected MCP tool as a registry entry whose handler
proxies back to ``MCPClientManager.call_tool``. Names are namespaced
``mcp__<server>__<tool>`` (MCP convention) so they never collide with built-in
tools, and the namespace is recoverable for routing the call back to the right
server.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Toolset label for everything bridged from external MCP servers.
MCP_TOOLSET = "mcp_external"


def mcp_tool_name(server: str, tool: str) -> str:
    """Namespaced registry name for an external MCP tool.

    ``mcp__<server>__<tool>`` — matches the MCP naming convention and lets
    ``split_mcp_tool_name`` recover (server, tool) when routing the call.
    """
    return f"mcp__{server}__{tool}"


def split_mcp_tool_name(name: str) -> tuple[str, str] | None:
    """Inverse of ``mcp_tool_name``. Returns (server, tool) or None if not ours.

    Server and tool segments are split on the FIRST ``__`` after the prefix, so a
    tool name that itself contains ``__`` is preserved intact.
    """
    if not name.startswith("mcp__"):
        return None
    rest = name[len("mcp__"):]
    server, sep, tool = rest.partition("__")
    if not sep or not server or not tool:
        return None
    return server, tool


def _schema_from_mcp(tool: dict) -> dict:
    """Convert an MCP tool's inputSchema to the registry's schema shape.

    MCP ``inputSchema`` is already JSON Schema; the registry wants an object with
    ``properties``/``required``. Fall back to an empty object schema when absent.
    """
    input_schema = tool.get("inputSchema") or {}
    return {
        "type": "object",
        "properties": input_schema.get("properties", {}),
        "required": input_schema.get("required", []),
    }


def register_mcp_tools(manager: Any, registry: Any) -> int:
    """Register all connected MCP tools into the registry. Returns count added.

    Each bridged tool's handler proxies to ``manager.call_tool(server, tool, args)``.
    Safe to call again after connecting more servers — re-registering the same
    name just refreshes it (same toolset, so the registry's anti-shadowing allows
    it).
    """
    tools = manager.get_all_tools()
    count = 0
    for t in tools:
        server = t.get("server", "")
        tool = t.get("name", "")
        if not server or not tool:
            continue

        reg_name = mcp_tool_name(server, tool)

        # Registry dispatch calls handlers as ``handler(args_dict, **kwargs)`` — the
        # tool arguments arrive as the FIRST POSITIONAL dict (see ToolRegistry.dispatch).
        # ``_server``/``_tool`` are keyword-only with per-iteration defaults so each
        # handler routes to its own server (avoids the late-binding closure bug)
        # without shadowing that positional ``params``.
        async def _handler(params: dict, *, _server=server, _tool=tool, **_: Any) -> str:
            return await manager.call_tool(_server, _tool, params or {})

        registry.register(
            name=reg_name,
            toolset=MCP_TOOLSET,
            schema=_schema_from_mcp(t),
            handler=_handler,
            is_async=True,
            description=t.get("description", "") or f"MCP tool {tool} from {server}",
            emoji="🔌",
            category=MCP_TOOLSET,
        )
        count += 1

    if count:
        # Force the LLM-facing schema cache to regenerate even when the tool COUNT
        # is unchanged (re-bridging the same server refreshes schemas). Without this
        # the cache — keyed on tool count — would serve stale schemas. Mirrors what
        # self_evolve does after scaffolding a tool.
        try:
            from app.mcp.tool_executor import ToolExecutor
            ToolExecutor.invalidate_schema_cache()
        except Exception:
            pass
        logger.info("mcp_tools_bridged", count=count)
    return count
