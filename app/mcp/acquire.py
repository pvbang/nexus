"""Self-acquire MCP capability when an agent needs a tool it doesn't have.

Closes the loop the operator asked for (audit #1): "thiếu tool → tìm MCP →
kết nối → dùng". Composes the phase-2 catalog (find the right server for a need)
with the phase-1 plumbing (connect + bridge tools into the registry).

The matching half lives in ``mcp_catalog`` (pure, tested). This module is the
thin I/O orchestration: pick the best catalog match for a need, connect it via
the existing ``MCPClientManager``, and bridge its tools so agents can call them.

Safety: only servers from the curated catalog are connectable here — no running
of arbitrary packages discovered online.
"""

from __future__ import annotations

from typing import Any

import structlog

from app.mcp.mcp_catalog import find_servers_for_need, get_catalog_entry

logger = structlog.get_logger(__name__)


def _missing_required_env(requires_env: tuple[str, ...]) -> list[str]:
    """Return required env vars that are absent/empty (can't connect without them)."""
    import os

    return [k for k in requires_env if not os.environ.get(k)]


async def acquire_capability(
    need: str,
    manager: Any,
    registry: Any,
    *,
    auto_connect: bool = True,
) -> dict:
    """Find — and optionally connect — an MCP server matching a capability need.

    Returns a result dict describing the outcome so the caller (agent/orchestrator)
    can act on it:
      - {"status": "no_match"}                       — nothing in the catalog fits
      - {"status": "needs_env", "server", "missing"} — match found but creds absent
      - {"status": "proposed", "server", ...}        — match found, auto_connect off
      - {"status": "connected", "server", "tools"}   — connected + tools bridged
      - {"status": "error", "server", "error"}       — connect/bridge failed

    Pure matching is delegated to the catalog; this only adds the connect/bridge
    I/O. ``auto_connect=False`` lets a caller surface a proposal for approval first.
    """
    matches = find_servers_for_need(need)
    if not matches:
        logger.info("mcp_acquire_no_match", need=need[:120])
        return {"status": "no_match"}

    best = matches[0].entry

    missing_env = _missing_required_env(best.requires_env)
    if missing_env:
        logger.info("mcp_acquire_needs_env", server=best.name, missing=missing_env)
        return {"status": "needs_env", "server": best.name, "missing": missing_env}

    if not auto_connect:
        return {
            "status": "proposed",
            "server": best.name,
            "package": best.package,
            "description": best.description,
        }

    try:
        from app.mcp.client import MCPServerConfig
        from app.mcp.external_bridge import register_mcp_tools

        # Idempotent: if this server is already connected, don't spawn another
        # subprocess — just (re)bridge its tools. Calling connect_server again
        # would overwrite the client and leak the previous stdio process.
        already = best.name in getattr(manager, "clients", {})
        if not already:
            cfg_dict = best.to_server_config()
            config = MCPServerConfig(
                name=cfg_dict["name"],
                command=cfg_dict.get("command", ""),
                args=cfg_dict.get("args", []),
                url=cfg_dict.get("url"),
                env=cfg_dict.get("env"),
                enabled=True,
            )
            ok = await manager.connect_server(config)
            if not ok:
                return {"status": "error", "server": best.name, "error": "connect_failed"}

        bridged = register_mcp_tools(manager, registry)
        logger.info(
            "mcp_acquire_connected", server=best.name, tools_bridged=bridged, reused=already,
        )
        return {"status": "connected", "server": best.name, "tools": bridged, "reused": already}
    except Exception as e:  # noqa: BLE001 — surface any connect/bridge failure to caller
        logger.warning("mcp_acquire_failed", server=best.name, error=str(e)[:200])
        return {"status": "error", "server": best.name, "error": str(e)[:200]}


def describe_catalog() -> str:
    """Human-readable catalog summary — useful for prompts / proposals."""
    from app.mcp.mcp_catalog import KNOWN_MCP_SERVERS

    lines = ["Available MCP servers (self-discoverable):"]
    for e in KNOWN_MCP_SERVERS:
        env = f" [needs {', '.join(e.requires_env)}]" if e.requires_env else ""
        lines.append(f"- {e.name}: {e.description}{env}")
    return "\n".join(lines)


# Re-export for convenience.
__all__ = ["acquire_capability", "describe_catalog", "get_catalog_entry"]
