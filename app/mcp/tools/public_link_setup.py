"""
MekongAI Nexus - Public Link Setup Tools.

Implements a "one-call" workflow:
- detect which backend port is actually serving this app
- optionally open firewall for that port (best-effort)
- optionally expose a public URL via cloudflared (no auth)
- create a tokenized public inbox link (UI + API)

This is designed to reduce friction for "connect to the real world" demos.
"""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import structlog

from app.config import get_settings
from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)


async def _probe_health(port: int) -> bool:
    """Return True if localhost:{port}/health looks like this app."""
    try:
        import httpx

        url = f"http://127.0.0.1:{port}/health"
        async with httpx.AsyncClient(timeout=2.0, follow_redirects=True) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return False
        try:
            data = r.json()
        except Exception:
            return False
        return data.get("status") == "healthy"
    except Exception:
        return False


async def _detect_backend_port(preferred: Optional[int] = None) -> int | None:
    s = get_settings()
    candidates: list[int] = []

    def _add(v: Any) -> None:
        try:
            n = int(v)
            if 1 <= n <= 65535 and n not in candidates:
                candidates.append(n)
        except Exception:
            pass

    if preferred:
        _add(preferred)

    # Common env vars / config
    _add(getattr(s, "app_port", None))
    _add(os.environ.get("APP_PORT"))
    _add(os.environ.get("PORT"))
    _add(os.environ.get("NEXUS_BACKEND_PORT"))

    # Common defaults in this repo
    for p in (8000, 8899, 3099, 3000, 18789):
        _add(p)

    for p in candidates:
        if await _probe_health(p):
            return p

    return None


def _in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("RUNNING_IN_DOCKER") == "1"


def _env_int(*names: str) -> int | None:
    for n in names:
        v = os.environ.get(n, "")
        try:
            x = int(str(v).strip())
            if 1 <= x <= 65535:
                return x
        except Exception:
            continue
    return None


def _detect_backend_external_port(default_internal: int) -> int:
    # Docker compose maps host port -> container 8000. Inside the container,
    # NEXUS_BACKEND_PORT / NEXUS_PORT represent the HOST port, which is what
    # the user should use for direct access (non-tunnel).
    ext = _env_int("NEXUS_BACKEND_PORT", "NEXUS_PORT", "NEXUS_APP_PORT")
    return int(ext or default_internal)


def _detect_gateway_external_port(default_port: int = 18800) -> int:
    ext = _env_int("NEXUS_PUBLIC_GATEWAY_PORT")
    return int(ext or default_port)


async def _resolve_direct_base_url(port: int) -> str:
    """Compute a direct (non-tunnel) base URL for humans.

    Priority:
    1) EXTERNAL_BASE_URL (explicit)
    2) NEXUS_PUBLIC_HOST (explicit)
    3) Public IP (best-effort via ipify)
    4) DevServerManager fallback
    """
    s = get_settings()
    base = (getattr(s, "external_base_url", "") or "").strip()
    if base:
        b = base.rstrip("/")
        try:
            p = urlparse(b)
            if p.scheme and p.netloc:
                # If a port is already present, don't force-replace it.
                if ":" in p.netloc:
                    return b
                return f"{b}:{port}"
        except Exception:
            pass
        # If user supplied a bare host, normalize to http.
        if "://" not in b:
            b = "http://" + b
        return f"{b}:{port}"

    public_host = (getattr(s, "public_host", "") or "").strip()
    if public_host:
        return f"http://{public_host}:{port}"

    # Best-effort public IP lookup (works on typical VPSes).
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.5, follow_redirects=True) as client:
            r = await client.get("https://api.ipify.org?format=json")
            if r.status_code == 200:
                ip = (r.json() or {}).get("ip", "")
                if isinstance(ip, str) and ip.strip():
                    return f"http://{ip.strip()}:{port}"
    except Exception:
        pass

    # Last resort: reuse preview URL logic (may return Docker internal gateway).
    try:
        from app.project_builder.dev_server import DevServerManager

        return DevServerManager._resolve_external_url(port)
    except Exception:
        host = getattr(s, "public_host", "") or "localhost"
        return f"http://{host}:{port}"


async def _open_firewall_port(port: int) -> dict[str, Any]:
    """Best-effort firewall opening (may require admin/root)."""
    s = get_settings()
    if not bool(getattr(s, "auto_open_firewall_port", True)):
        return {"attempted": False, "ok": False, "reason": "auto_open_firewall_port=false"}

    system = platform.system().lower()

    # In Docker, opening host firewall is usually impossible/meaningless.
    in_docker = Path("/.dockerenv").exists() or os.environ.get("RUNNING_IN_DOCKER") == "1"
    if in_docker:
        return {"attempted": False, "ok": False, "reason": "running_in_docker"}

    # Windows: netsh (widely available). Requires elevated privileges.
    if system.startswith("win"):
        rule_name = f"MekongAI Nexus {port}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "netsh",
                "advfirewall",
                "firewall",
                "add",
                "rule",
                f"name={rule_name}",
                "dir=in",
                "action=allow",
                "protocol=TCP",
                f"localport={port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            ok = proc.returncode == 0
            return {
                "attempted": True,
                "ok": ok,
                "method": "netsh",
                "stdout": (out or b"").decode("utf-8", errors="replace")[:500],
                "stderr": (err or b"").decode("utf-8", errors="replace")[:500],
                "returncode": proc.returncode,
            }
        except FileNotFoundError:
            return {"attempted": True, "ok": False, "reason": "netsh not found"}
        except Exception as e:
            return {"attempted": True, "ok": False, "reason": f"{type(e).__name__}: {str(e)[:200]}"}

    # Linux: try ufw then iptables (same approach as DevServerManager).
    if system.startswith("linux"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "ufw", "allow", str(port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            if proc.returncode == 0:
                return {"attempted": True, "ok": True, "method": "ufw"}
        except Exception:
            pass

        try:
            proc = await asyncio.create_subprocess_exec(
                "iptables", "-I", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            if proc.returncode == 0:
                return {"attempted": True, "ok": True, "method": "iptables"}
        except Exception:
            pass

        return {"attempted": True, "ok": False, "reason": "no ufw/iptables or insufficient permissions"}

    return {"attempted": False, "ok": False, "reason": f"unsupported platform: {platform.system()}"}


async def _setup_public_inbox(params: dict[str, Any], **kwargs: Any) -> str:
    label = (params.get("label") or "").strip()
    conversation_id = (params.get("conversation_id") or "").strip() or None
    preferred_port = params.get("port")
    use_tunnel = bool(params.get("use_tunnel", True))
    open_firewall = bool(params.get("open_firewall", True))
    via_gateway = bool(params.get("via_gateway", False))

    # 1) Create tokenized inbox
    from app.core.public_inbox import create_public_inbox

    inbox = create_public_inbox(label=label, conversation_id=conversation_id)
    backend_path = f"/api/v1/public/inbox/{inbox.token}"
    gateway_path = f"/inbox/{inbox.token}"

    # 2) Detect backend port (the one that actually serves /health)
    backend_internal_port = (
        await _detect_backend_port(int(preferred_port))
        if preferred_port is not None
        else await _detect_backend_port()
    )
    if not backend_internal_port:
        # Fall back to settings.app_port without probing.
        backend_internal_port = int(getattr(get_settings(), "app_port", 8000))

    backend_external_port = int(params.get("external_port") or 0) or _detect_backend_external_port(int(backend_internal_port))

    # 3) Firewall (best-effort)
    fw = {"attempted": False, "ok": False, "reason": "open_firewall=false"}
    if open_firewall:
        # Use ToolExecutor so tool policy/guard can control this action.
        try:
            from app.mcp.tool_executor import ToolExecutor

            ex = ToolExecutor()
            # Open external port if provided; in non-Docker it's usually the same.
            r = await ex.execute("open_firewall_port", {"port": int(backend_external_port)})
            fw = r.get("data") if isinstance(r, dict) and isinstance(r.get("data"), dict) else fw
        except Exception as e:
            fw = {"attempted": True, "ok": False, "reason": f"{type(e).__name__}: {str(e)[:200]}"}

    # 4) Direct base URL (no tunnel)
    backend_direct_base = await _resolve_direct_base_url(int(backend_external_port))
    backend_direct_url = backend_direct_base.rstrip("/") + backend_path

    tunnel_url: str | None = None
    tunnel_base: str | None = None
    if use_tunnel:
        try:
            # Use ToolExecutor so tool policy/guard can control this action.
            from app.mcp.tool_executor import ToolExecutor

            ex = ToolExecutor()
            r = await ex.execute("expose_port", {"port": int(backend_internal_port)})
            tunnel_base = (r.get("data") or {}).get("url") if isinstance(r, dict) else None
            if tunnel_base:
                tunnel_url = str(tunnel_base).rstrip("/") + backend_path
        except Exception as e:
            logger.warning("setup_public_inbox_tunnel_failed", error=str(e)[:200])

    gateway_direct_url: str | None = None
    gateway_tunnel_url: str | None = None
    gateway_tunnel_base: str | None = None
    gateway_meta: dict[str, Any] = {}

    if via_gateway:
        # Direct URL for gateway (host port)
        gateway_external_port = int(params.get("gateway_external_port") or 0) or _detect_gateway_external_port(18800)
        gateway_direct_base = await _resolve_direct_base_url(int(gateway_external_port))
        gateway_direct_url = gateway_direct_base.rstrip("/") + gateway_path

        # Best-effort open firewall for the gateway port too (non-Docker only).
        if open_firewall:
            try:
                from app.mcp.tool_executor import ToolExecutor

                ex = ToolExecutor()
                _ = await ex.execute("open_firewall_port", {"port": int(gateway_external_port)})
            except Exception:
                pass

        # Tunnel URL for gateway (internal Docker network), if reachable.
        # In docker-compose, service name is "nexus-gateway".
        gw_host = (params.get("gateway_internal_host") or "").strip()
        if not gw_host:
            gw_host = "nexus-gateway" if _in_docker() else "127.0.0.1"
        gw_port = int(params.get("gateway_internal_port") or 18800)

        # Probe gateway health before tunneling to avoid creating a dead tunnel.
        gw_ok = False
        try:
            import httpx
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"http://{gw_host}:{gw_port}/health")
                gw_ok = r.status_code == 200
        except Exception:
            gw_ok = False

        if use_tunnel and gw_ok:
            try:
                from app.mcp.tool_executor import ToolExecutor

                ex = ToolExecutor()
                r = await ex.execute("expose_port", {"port": int(gw_port), "host": gw_host})
                gateway_tunnel_base = (r.get("data") or {}).get("url") if isinstance(r, dict) else None
                if gateway_tunnel_base:
                    gateway_tunnel_url = str(gateway_tunnel_base).rstrip("/") + gateway_path
            except Exception as e:
                logger.warning("setup_public_gateway_tunnel_failed", error=str(e)[:200])

        gateway_meta = {
            "gateway_external_port": int(gateway_external_port),
            "gateway_internal_host": gw_host,
            "gateway_internal_port": int(gw_port),
            "gateway_health_ok": bool(gw_ok),
        }

    lines = ["Public inbox setup:"]
    lines.append(f"- Backend internal port: {backend_internal_port} (for tunnel)")
    lines.append(f"- Backend external port: {backend_external_port} (for direct access)")
    lines.append(f"- Backend path: {backend_path} (GET = UI form, POST = JSON)")
    lines.append(f"- Backend direct URL: {backend_direct_url}")
    if tunnel_url:
        lines.append(f"- Backend tunnel URL: {tunnel_url}")
    else:
        lines.append("- Backend tunnel URL: (not created; use_tunnel=false or tunnel failed)")
    lines.append(f"- Firewall: attempted={fw.get('attempted')} ok={fw.get('ok')} reason={fw.get('reason') or ''}")

    if via_gateway:
        lines.append("")
        lines.append("Gateway (separate port, minimal public surface):")
        if gateway_direct_url:
            lines.append(f"- Gateway direct URL: {gateway_direct_url}")
        if gateway_tunnel_url:
            lines.append(f"- Gateway tunnel URL: {gateway_tunnel_url}")
        else:
            lines.append("- Gateway tunnel URL: (not created; gateway not reachable or tunnel failed)")

    return tool_result(
        success=True,
        output="\n".join(lines),
        data={
            "token": inbox.token,
            "conversation_id": inbox.conversation_id,
            "backend_path": backend_path,
            "gateway_path": gateway_path,
            "backend_internal_port": int(backend_internal_port),
            "backend_external_port": int(backend_external_port),
            "backend_direct_base_url": backend_direct_base,
            "backend_direct_url": backend_direct_url,
            "backend_tunnel_base_url": tunnel_base,
            "backend_tunnel_url": tunnel_url,
            "gateway_direct_url": gateway_direct_url,
            "gateway_tunnel_base_url": gateway_tunnel_base,
            "gateway_tunnel_url": gateway_tunnel_url,
            "gateway": gateway_meta,
            "firewall": fw,
        },
    )


registry.register(
    name="setup_public_inbox",
    toolset="communication",
    schema={
        "description": (
            "One-call setup: detect backend port, optionally open firewall, optionally expose via tunnel, "
            "create a tokenized public inbox, and return direct+tunnel URLs. "
            "GET on the URL shows a simple HTML form; POST accepts JSON."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Optional label"},
                "conversation_id": {"type": "string", "description": "Optional fixed conversation_id"},
                "port": {"type": "integer", "description": "Optional preferred backend port (auto-detect if omitted)"},
                "external_port": {"type": "integer", "description": "Optional external/host port for direct URL (auto-detect if omitted)"},
                "use_tunnel": {"type": "boolean", "default": True, "description": "Create a trycloudflare public URL via expose_port"},
                "open_firewall": {"type": "boolean", "default": True, "description": "Attempt to open firewall for the backend port"},
                "via_gateway": {"type": "boolean", "default": False, "description": "Also return a URL on the Public Gateway port (/inbox/{token})"},
                "gateway_external_port": {"type": "integer", "description": "Optional external/host port for the Public Gateway (default: NEXUS_PUBLIC_GATEWAY_PORT or 18800)"},
                "gateway_internal_host": {"type": "string", "description": "Optional internal host for tunneling the gateway (default: nexus-gateway in Docker, 127.0.0.1 otherwise)"},
                "gateway_internal_port": {"type": "integer", "description": "Optional internal port for gateway health/tunnel (default 18800)"},
            },
        },
    },
    handler=_setup_public_inbox,
    is_async=True,
    emoji="*",
    category="communication",
)


async def _handle_open_firewall_port(params: dict[str, Any], **kwargs: Any) -> str:
    port = int(params.get("port") or 0)
    if port <= 0 or port > 65535:
        return tool_error("port must be 1..65535")
    data = await _open_firewall_port(port)
    msg = (
        f"Firewall open port {port}: attempted={data.get('attempted')} ok={data.get('ok')} "
        f"reason={data.get('reason') or ''}"
    )
    return tool_result(success=bool(data.get("ok")), output=msg, data=data)

registry.register(
    name="open_firewall_port",
    toolset="security",
    schema={
        "description": "Best-effort open inbound firewall rule for a TCP port (may require admin/root).",
        "parameters": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "TCP port to allow inbound"},
            },
            "required": ["port"],
        },
    },
    handler=_handle_open_firewall_port,
    is_async=True,
    emoji="*",
    category="security",
)
