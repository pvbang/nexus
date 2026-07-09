"""Hot-reload MCP server configuration without restarting the app.

MCP servers are connected once at startup from ``settings.mcp_servers`` (env
JSON) or an optional ``.mcp.json`` file. When that config changes, the old
behaviour required a full restart. This module adds:

- ``MCPConfigWatcher``: mtime-gated change detection for a config file
  (mirrors deer-flow's ``mcp/cache.py`` mtime invalidation).
- ``diff_server_configs``: pure diff of current vs desired servers
  (added / removed / changed) so a reload only touches what actually changed.
- ``reload_mcp_servers``: apply the diff against a live MCPClientManager —
  disconnect removed/changed, connect added/changed, re-bridge tools.

A server is "removed" if it disappears OR becomes ``enabled=False``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


def _identity(cfg: Any) -> tuple:
    """Fields that, if changed, require a reconnect."""
    return (
        getattr(cfg, "command", "") or "",
        tuple(getattr(cfg, "args", []) or []),
        getattr(cfg, "url", None),
    )


@dataclass
class ConfigDiff:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)


def diff_server_configs(current: list[Any], desired: list[Any]) -> ConfigDiff:
    """Diff two lists of MCPServerConfig-like objects by name + identity.

    A desired server with ``enabled=False`` is treated as removed.
    """
    cur_by_name = {c.name: c for c in current}
    des_enabled = {c.name: c for c in desired if getattr(c, "enabled", True)}
    des_disabled = {c.name for c in desired if not getattr(c, "enabled", True)}

    diff = ConfigDiff()
    for name, dcfg in des_enabled.items():
        if name not in cur_by_name:
            diff.added.append(name)
        elif _identity(cur_by_name[name]) != _identity(dcfg):
            diff.changed.append(name)

    for name in cur_by_name:
        if name in des_disabled or name not in des_enabled:
            diff.removed.append(name)

    return diff


class MCPConfigWatcher:
    """Detects changes to an MCP config file via mtime (no polling thread)."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._last_mtime: Optional[float] = None

    def changed(self) -> bool:
        """Return True if the file's mtime advanced since the last check.

        Missing file → False (nothing to reload). The first successful check
        after construction reports True so callers can do an initial load.
        """
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            return False
        if self._last_mtime is None or mtime > self._last_mtime:
            self._last_mtime = mtime
            return True
        return False


async def reload_mcp_servers(manager: Any, desired: list[Any], registry: Any) -> ConfigDiff:
    """Apply ``desired`` config to a live manager: disconnect/connect the delta.

    Returns the diff that was applied. Best-effort per server; a single failure
    is logged and does not abort the rest.
    """
    current = [c.config for c in manager.clients.values()] if hasattr(manager, "clients") else []
    diff = diff_server_configs(current, desired)

    if diff.is_empty:
        return diff

    des_by_name = {c.name: c for c in desired}

    # Disconnect removed + changed (changed servers reconnect below).
    for name in diff.removed + diff.changed:
        try:
            await manager.disconnect_server(name)
        except Exception as exc:
            logger.warning("mcp_reload_disconnect_failed", server=name, error=str(exc))

    # Connect added + changed.
    for name in diff.added + diff.changed:
        cfg = des_by_name.get(name)
        if cfg is None:
            continue
        try:
            await manager.connect_server(cfg)
        except Exception as exc:
            logger.warning("mcp_reload_connect_failed", server=name, error=str(exc))

    # Re-bridge tools into the registry so changes are visible to agents.
    try:
        from app.mcp.external_bridge import register_mcp_tools
        register_mcp_tools(manager, registry)
    except Exception as exc:
        logger.warning("mcp_reload_rebridge_failed", error=str(exc))

    logger.info("mcp_servers_reloaded", added=diff.added, removed=diff.removed, changed=diff.changed)
    return diff
