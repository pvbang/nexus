"""
Nexus - Dev Server Management Tools.

Start/stop/manage development servers for project preview.
Wraps DevServerManager for tool-calling by agents.

Tools registered:
    - start_dev_server: Start a development server
    - stop_dev_server: Stop a running dev server
    - list_dev_servers: List all running dev servers
    - get_server_logs: Get recent logs from a dev server
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import structlog

from app.core.command_guard import check_path_safety
from app.mcp.tool_registry import registry

logger = structlog.get_logger(__name__)

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
WORKSPACE_DIR.mkdir(exist_ok=True)

# Allowed port range for dev servers (must match Docker EXPOSE)
_PORT_RANGE_MIN = 19000
_PORT_RANGE_MAX = 19049

# Singleton DevServerManager instance (lazy-initialized)
_dev_server_mgr: Any = None


def _get_mgr() -> Any:
    """Get or create the singleton DevServerManager instance."""
    global _dev_server_mgr
    if _dev_server_mgr is None:
        from app.project_builder.dev_server import DevServerManager

        _dev_server_mgr = DevServerManager()
    return _dev_server_mgr


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------


def _is_port_in_allowed_range(port: int) -> bool:
    """Check if port is within the allowed exposed range 19000-19049."""
    return _PORT_RANGE_MIN <= port <= _PORT_RANGE_MAX


async def handle_start_dev_server(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Start a development server for a project.

    Resolves the project path within workspace, then delegates
    to DevServerManager.start_server with port auto-allocation.

    Port enforcement: If port is 0 or outside 19000-19049, it will
    be overridden to auto-allocate from the allowed range.
    """
    project_path_str = params.get("project_path", "")
    tech = params.get("tech", "")
    port = params.get("port", 0)

    if not project_path_str:
        return {"success": False, "output": "Missing required param: project_path", "data": None, "artifacts": []}
    if not tech:
        return {"success": False, "output": "Missing required param: tech", "data": None, "artifacts": []}

    # ENFORCE port range: reject ports outside 19000-19049, force auto-allocate
    if port != 0 and not _is_port_in_allowed_range(port):
        logger.warning(
            "dev_server_port_override",
            requested_port=port,
            reason=f"Port {port} outside allowed range {_PORT_RANGE_MIN}-{_PORT_RANGE_MAX}, will auto-allocate",
        )
        port = 0  # Force auto-allocate from allowed range

    # Validate path
    verdict = check_path_safety(project_path_str, str(WORKSPACE_DIR))
    if not verdict.safe:
        return {"success": False, "output": f"⚠️ Path blocked: {verdict.reason}", "data": None, "artifacts": []}

    project_path = WORKSPACE_DIR / project_path_str
    if not project_path.exists():
        return {"success": False, "output": f"Project path not found: {project_path_str}", "data": None, "artifacts": []}
    if not project_path.is_dir():
        return {"success": False, "output": f"Not a directory: {project_path_str}", "data": None, "artifacts": []}

    # Start server via DevServerManager
    mgr = _get_mgr()
    try:
        info = await mgr.start_server(
            project_path=str(project_path),
            tech=tech,
            port=port,
        )
    except RuntimeError as exc:
        logger.error("dev_server_start_failed", project_path=project_path_str, tech=tech, error=str(exc))
        return {
            "success": False,
            "output": f"Failed to start {tech} server: {exc}",
            "data": None,
            "artifacts": [],
        }

    logger.info(
        "dev_server_started_via_tool",
        server_id=info.server_id,
        url=info.url,
        tech=tech,
        status=info.status,
    )

    # Best-effort: create a real public URL via tunnel when running behind Docker/NAT.
    public_url: str = ""
    tunnel_error: str = ""  # surfaced to the agent so a silent failure isn't mistaken for success
    want_tunnel = False
    in_docker = Path("/.dockerenv").exists() or os.environ.get("RUNNING_IN_DOCKER") == "1"
    try:
        from app.config import get_settings

        s = get_settings()
        has_explicit_external = bool(
            (s.external_base_url or "").strip() or (s.public_host or "").strip()
        )
        want_tunnel = (
            bool(params.get("use_tunnel", False))
            or (in_docker and not has_explicit_external)
        )
        if want_tunnel:
            from app.mcp.tool_executor import ToolExecutor

            ex = ToolExecutor()
            r = await ex.execute("expose_port", {"port": int(info.port)})
            url = (r.get("data") or {}).get("url") if isinstance(r, dict) else None
            if url:
                public_url = str(url).strip()
                info.public_url = public_url
            else:
                # Don't swallow: record WHY there's no public URL so the agent can
                # tell the user instead of handing out an unreachable internal URL.
                tunnel_error = (
                    (r.get("error") if isinstance(r, dict) else None)
                    or "tunnel did not return a URL (cloudflared unavailable or blocked)"
                )
    except Exception as e:
        tunnel_error = f"tunnel attempt failed: {str(e)[:200]}"

    # Preview-via-domain: if this Nexus instance is reachable at a domain, the most
    # reliable link is through its own reverse-proxy (/preview/<port>/) — no tunnel,
    # no extra firewall/DNS. Build the full URL when a domain is configured.
    domain_preview_url = ""
    preview_path = f"/preview/{info.port}/"
    try:
        from app.config import get_settings
        from app.core.public_base import resolve_public_base
        _s = get_settings()
        # Precedence: EXTERNAL_BASE_URL → NEXUS_PUBLIC_HOST → domain auto-learned
        # from incoming requests (so no manual .env needed in the common case).
        _base = resolve_public_base(_s.external_base_url, _s.public_host)
        if _base:
            domain_preview_url = f"{_base.rstrip('/')}{preview_path}"
    except Exception:
        pass

    out = "Dev server started!\n"
    if domain_preview_url:
        out += f"🌐 Preview URL (via domain — give THIS to the user): {domain_preview_url}\n"
    if public_url:
        out += f"🌐 Public URL (tunnel): {public_url}\n"
    elif want_tunnel and not domain_preview_url:
        # Tunnel was wanted (Docker/NAT) but failed → the external_url below is
        # likely an internal address the user CANNOT reach. Make that explicit.
        out += (
            f"⚠️ Public tunnel unavailable: {tunnel_error}\n"
            "   The External URL below may be internal-only (user may not be able to reach it).\n"
            "   To give the user a working link: set NEXUS_PUBLIC_HOST/EXTERNAL_BASE_URL to a\n"
            "   reachable host, or call expose_port(port) once cloudflared is available.\n"
        )
    # When a domain preview URL exists it is THE link for the user. external_url is
    # base:{port} (e.g. https://domain:19000) which is a DEAD link behind a reverse
    # proxy — port 19000 isn't published on 443. So don't label it "for user access"
    # (and don't let it look like a working link the agent should hand over).
    if domain_preview_url:
        out += (
            f"🔧 Internal URL (health checks only): {info.url}\n"
            f"Server ID: {info.server_id}\n"
            f"Tech: {tech} | Port: {info.port} | Status: {info.status}\n"
            "IMPORTANT: Give the user the Preview URL (via domain) above. Do NOT hand out "
            "localhost or a host:port URL — those are not reachable from outside."
        )
    else:
        out += (
            f"🌐 External URL (for user access): {info.external_url}\n"
            f"🔧 Internal URL (for health checks): {info.url}\n"
            f"Server ID: {info.server_id}\n"
            f"Tech: {tech} | Port: {info.port} | Status: {info.status}\n"
            "IMPORTANT: Provide Public URL (tunnel) if present, otherwise External URL; never localhost."
        )

    return {
        "success": True,
        "output": out,
        "data": {
            "server_id": info.server_id,
            # Primary URL for user: domain preview (most reliable) → tunnel → external.
            "url": domain_preview_url or public_url or info.external_url,
            "internal_url": info.url,
            "external_url": info.external_url,
            "public_url": public_url or "",
            "domain_preview_url": domain_preview_url,  # via this Nexus's /preview proxy
            "preview_path": preview_path,              # domain-relative, ghép với host
            "tunnel_error": tunnel_error,  # non-empty when a wanted tunnel failed
            "port": info.port,
            "tech": tech,
            "status": info.status,
            "pid": info.pid,
            "project_path": project_path_str,
        },
        "artifacts": [],
    }


async def handle_stop_dev_server(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Stop a running dev server by its server ID."""
    server_id = params.get("server_id", "")

    if not server_id:
        return {"success": False, "output": "Missing required param: server_id", "data": None, "artifacts": []}

    mgr = _get_mgr()
    stopped = await mgr.stop_server(server_id)

    if not stopped:
        return {
            "success": False,
            "output": f"Server not found or already stopped: {server_id}",
            "data": {"server_id": server_id},
            "artifacts": [],
        }

    logger.info("dev_server_stopped_via_tool", server_id=server_id)

    return {
        "success": True,
        "output": f"Server stopped: {server_id}",
        "data": {"server_id": server_id, "status": "stopped"},
        "artifacts": [],
    }


async def handle_list_dev_servers(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """List all running dev servers with their status."""
    mgr = _get_mgr()
    servers = mgr.list_servers()

    if not servers:
        return {
            "success": True,
            "output": "No dev servers running.",
            "data": {"servers": [], "count": 0},
            "artifacts": [],
        }

    server_list: list[dict[str, Any]] = []
    output_lines: list[str] = [f"Running dev servers ({len(servers)}):"]

    for info in servers:
        server_data = {
            "server_id": info.server_id,
            "url": info.external_url or info.url,
            "internal_url": info.url,
            "external_url": info.external_url,
            "port": info.port,
            "tech": info.tech,
            "status": info.status,
            "pid": info.pid,
            "project_path": info.project_path,
        }
        server_list.append(server_data)
        display_url = info.external_url or info.url
        output_lines.append(
            f"  • {info.server_id}: {display_url} ({info.tech}) [{info.status}]"
        )

    return {
        "success": True,
        "output": "\n".join(output_lines),
        "data": {"servers": server_list, "count": len(server_list)},
        "artifacts": [],
    }


async def handle_get_server_logs(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Get recent logs from a dev server's stdout/stderr."""
    server_id = params.get("server_id", "")
    lines = params.get("lines", 50)

    if not server_id:
        return {"success": False, "output": "Missing required param: server_id", "data": None, "artifacts": []}

    # Clamp lines to reasonable range
    lines = max(1, min(lines, 500))

    mgr = _get_mgr()

    # Check server exists
    server_info = mgr.get_server(server_id)
    if server_info is None:
        return {
            "success": False,
            "output": f"Server not found: {server_id}",
            "data": {"server_id": server_id},
            "artifacts": [],
        }

    log_text = await mgr.get_server_logs(server_id, lines=lines)

    if not log_text:
        return {
            "success": True,
            "output": f"No logs available yet for {server_id}",
            "data": {"server_id": server_id, "lines_returned": 0},
            "artifacts": [],
        }

    return {
        "success": True,
        "output": log_text,
        "data": {
            "server_id": server_id,
            "lines_returned": log_text.count("\n") + 1,
            "status": server_info.status,
        },
        "artifacts": [],
    }


# ------------------------------------------------------------------
# Registration
# ------------------------------------------------------------------

_TOOLSET = "dev_server"

registry.register(
    name="start_dev_server",
    toolset=_TOOLSET,
    schema={
        "description": (
            "Start a development server for a project. Supports: vite, next, "
            "fastapi, flask, express, django. Auto-allocates port from range 19000-19049. "
            "IMPORTANT: Only ports 19000-19049 are accessible to users (Docker EXPOSE). "
            "Any port outside this range will be auto-overridden."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "project_path": {
                    "type": "string",
                    "description": "Path to project directory (relative to workspace)",
                },
                "tech": {
                    "type": "string",
                    "description": "Technology/framework for the dev server",
                    "enum": ["vite", "next", "fastapi", "flask", "express", "django"],
                },
                "port": {
                    "type": "integer",
                    "description": (
                        "Port to use. MUST be 0 (auto-allocate) or within 19000-19049. "
                        "Ports outside this range are rejected and auto-allocated. Default: 0"
                    ),
                    "default": 0,
                },
                "use_tunnel": {
                    "type": "boolean",
                    "description": (
                        "If true, also create a real public URL via tunnel (trycloudflare) for this dev server. "
                        "If omitted, the tool may still auto-tunnel when running in Docker without EXTERNAL_BASE_URL/NEXUS_PUBLIC_HOST."
                    ),
                    "default": False,
                },
            },
            "required": ["project_path", "tech"],
        },
    },
    handler=handle_start_dev_server,
    is_async=True,
    emoji="🚀",
)

registry.register(
    name="stop_dev_server",
    toolset=_TOOLSET,
    schema={
        "description": "Stop a running development server by its server ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "string",
                    "description": "Server ID returned from start_dev_server",
                },
            },
            "required": ["server_id"],
        },
    },
    handler=handle_stop_dev_server,
    is_async=True,
    emoji="🛑",
)

registry.register(
    name="list_dev_servers",
    toolset=_TOOLSET,
    schema={
        "description": "List all running development servers with their URLs, ports, and status.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    handler=handle_list_dev_servers,
    is_async=True,
    emoji="📋",
)

registry.register(
    name="get_server_logs",
    toolset=_TOOLSET,
    schema={
        "description": "Get recent stdout/stderr logs from a running dev server. Useful for debugging startup errors.",
        "parameters": {
            "type": "object",
            "properties": {
                "server_id": {
                    "type": "string",
                    "description": "Server ID returned from start_dev_server",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of recent log lines to return (default: 50, max: 500)",
                    "default": 50,
                },
            },
            "required": ["server_id"],
        },
    },
    handler=handle_get_server_logs,
    is_async=True,
    emoji="📜",
)
