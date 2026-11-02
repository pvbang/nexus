"""
Nexus - Tunnel Tools for Public URL Exposure.

Provides MCP tools to expose local ports via tunnel providers,
enabling agents to make services accessible from the internet
when running behind NAT.

Providers (in priority order):
1. cloudflared — free, no auth needed (Cloudflare Tunnel)
2. bore — free, downloads binary from GitHub
3. ssh tunnel — requires TUNNEL_SSH_HOST env var
"""

import asyncio
import os
import platform
import re
import shutil
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog

from app.mcp.tool_registry import registry, tool_error, tool_result

logger = structlog.get_logger(__name__)

TOOLSET = "tunnel"
_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))
_BIN_DIR = _WORKSPACE_BASE / "bin"


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------


@dataclass
class TunnelInfo:
    """Metadata for an active tunnel process."""

    port: int
    url: str
    provider: str
    pid: int
    process: asyncio.subprocess.Process
    host: str = "localhost"


# Module-level state: active tunnels keyed by "<host>:<port>"
_active_tunnels: dict[str, TunnelInfo] = {}


def _tunnel_key(host: str, port: int) -> str:
    h = (host or "localhost").strip() or "localhost"
    return f"{h}:{int(port)}"


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


async def _ensure_cloudflared_available() -> str | None:
    """Ensure cloudflared exists (best-effort).

    - If system-installed: return shutil.which("cloudflared")
    - Else: download a platform-appropriate binary into WORKSPACE_DIR/bin and return it.

    This enables getting a real public URL (trycloudflare.com) without any API keys.
    """
    existing = shutil.which("cloudflared")
    if existing:
        return existing

    system = platform.system().lower()
    machine = platform.machine().lower()

    if system.startswith("win"):
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
        exe = _BIN_DIR / "cloudflared.exe"
    elif system.startswith("linux"):
        if "aarch64" in machine or "arm64" in machine:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64"
        else:
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
        exe = _BIN_DIR / "cloudflared"
    else:
        return None

    if exe.exists() and exe.stat().st_size > 100_000:
        return str(exe)

    try:
        import httpx

        _BIN_DIR.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            exe.write_bytes(resp.content)
        if not system.startswith("win"):
            exe.chmod(0o755)
        logger.info("cloudflared_downloaded", path=str(exe), url=url)
        return str(exe)
    except Exception as e:
        logger.warning("cloudflared_download_failed", error=str(e)[:200])
        return None


async def _extract_tunnel_url(
    stream: asyncio.StreamReader,
    timeout: float = 15.0,
    pattern: re.Pattern | None = None,
) -> str:
    """Read from stream until a tunnel URL is found or timeout."""
    if pattern is None:
        # cloudflared outputs URLs like https://xxx-xxx.trycloudflare.com
        pattern = re.compile(r"(https?://[a-zA-Z0-9._-]+\.trycloudflare\.com)")

    deadline = asyncio.get_event_loop().time() + timeout
    buffer = ""

    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            chunk = await asyncio.wait_for(stream.read(4096), timeout=remaining)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            buffer += text
            match = pattern.search(buffer)
            if match:
                return match.group(1)
        except asyncio.TimeoutError:
            break

    raise TimeoutError(
        f"Could not extract tunnel URL within {timeout}s. Output so far: {buffer[:500]}"
    )


async def _start_cloudflared(port: int, host: str = "localhost") -> dict[str, Any]:
    """Start cloudflared tunnel and return public URL."""
    cloudflared = shutil.which("cloudflared") or await _ensure_cloudflared_available()
    if not cloudflared:
        raise RuntimeError(
            "cloudflared not found and could not be downloaded automatically."
        )
    target_host = (host or "localhost").strip() or "localhost"
    proc = await asyncio.create_subprocess_exec(
        cloudflared,
        "tunnel",
        "--url",
        f"http://{target_host}:{port}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # cloudflared outputs the URL to stderr
    url = await _extract_tunnel_url(proc.stderr, timeout=15)
    info = TunnelInfo(
        port=port,
        host=target_host,
        url=url,
        provider="cloudflared",
        pid=proc.pid,
        process=proc,
    )
    _active_tunnels[_tunnel_key(target_host, port)] = info
    return {"url": url, "provider": "cloudflared", "pid": proc.pid, "port": port, "host": target_host}


async def _start_bore(port: int) -> dict[str, Any]:
    """Start bore tunnel and return public URL."""
    bore_host = os.environ.get("BORE_SERVER", "bore.pub")
    proc = await asyncio.create_subprocess_exec(
        "bore", "local", str(port), "--to", bore_host,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # bore outputs: "listening at bore.pub:XXXXX"
    pattern = re.compile(r"((?:https?://)?[a-zA-Z0-9._-]+:\d+)")
    url = await _extract_tunnel_url(proc.stdout, timeout=15, pattern=pattern)
    if not url.startswith("http"):
        url = f"http://{url}"
    info = TunnelInfo(port=port, host="localhost", url=url, provider="bore", pid=proc.pid, process=proc)
    _active_tunnels[_tunnel_key("localhost", port)] = info
    return {"url": url, "provider": "bore", "pid": proc.pid, "port": port, "host": "localhost"}


async def _start_ssh_tunnel(port: int) -> dict[str, Any]:
    """Start SSH remote tunnel using TUNNEL_SSH_HOST env."""
    ssh_host = os.environ.get("TUNNEL_SSH_HOST", "")
    if not ssh_host:
        raise RuntimeError("TUNNEL_SSH_HOST env not set")

    proc = await asyncio.create_subprocess_exec(
        "ssh", "-R", f"80:localhost:{port}", ssh_host,
        "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # SSH tunnels often echo the forwarded URL on stdout/stderr
    pattern = re.compile(r"(https?://[^\s]+)")
    try:
        url = await _extract_tunnel_url(proc.stdout, timeout=10, pattern=pattern)
    except TimeoutError:
        # If no URL detected, construct from host
        url = f"http://{ssh_host}"

    info = TunnelInfo(port=port, host="localhost", url=url, provider="ssh", pid=proc.pid, process=proc)
    _active_tunnels[_tunnel_key("localhost", port)] = info
    return {"url": url, "provider": "ssh", "pid": proc.pid, "port": port, "host": "localhost"}


def _detect_available_provider() -> str | None:
    """Auto-detect which tunnel binary is available (priority order)."""
    if shutil.which("cloudflared") or (_BIN_DIR / "cloudflared.exe").exists() or (_BIN_DIR / "cloudflared").exists():
        return "cloudflared"
    if shutil.which("bore"):
        return "bore"
    if os.environ.get("TUNNEL_SSH_HOST") and shutil.which("ssh"):
        return "ssh"
    return None


# ------------------------------------------------------------------
# Tool handlers
# ------------------------------------------------------------------


async def handle_expose_port(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Expose a local port via tunnel to get a public URL.

    Tries providers in priority: cloudflared > bore > ssh.
    """
    port_raw = params.get("port")
    try:
        port = int(port_raw)
    except Exception:
        port = 0
    if port <= 0 or port > 65535:
        return tool_error("port must be an integer in 1..65535")

    host = (params.get("host") or "localhost").strip() or "localhost"
    key = _tunnel_key(host, port)

    if key in _active_tunnels:
        info = _active_tunnels[key]
        return tool_result(
            success=True,
            output=f"Port {port} on {host} already exposed at {info.url} (provider: {info.provider})",
            data={
                "url": info.url,
                "provider": info.provider,
                "port": port,
                "host": host,
                "already_active": True,
            },
        )

    protocol = params.get("protocol", "http")
    subdomain = params.get("subdomain")  # reserved for future use
    _ = protocol, subdomain  # reserved for future use

    host_is_local = host in ("localhost", "127.0.0.1", "::1")

    # If targeting a non-local host (e.g. Docker service name), only cloudflared
    # can handle this reliably. Force provider selection accordingly.
    provider = "cloudflared" if not host_is_local else _detect_available_provider()
    if not provider:
        # Best-effort auto-install of cloudflared (no-auth provider).
        try:
            await _ensure_cloudflared_available()
        except Exception:
            pass
        provider = _detect_available_provider()
    if not host_is_local and provider != "cloudflared":
        provider = "cloudflared"

    if not provider:
        return tool_error(
            "No tunnel provider found. Install one:\n"
            "  • cloudflared: https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/\n"
            "  • bore: cargo install bore-cli (or download from https://github.com/ekzhang/bore)\n"
            "  • ssh: set TUNNEL_SSH_HOST env var (e.g. serveo.net or localhost.run)\n"
            "\n"
            "TIP: This tool can auto-download cloudflared into WORKSPACE_DIR/bin in many environments."
        )

    try:
        if provider == "cloudflared":
            result = await _start_cloudflared(port, host=host)
        elif provider == "bore":
            if host not in ("localhost", "127.0.0.1", "::1"):
                return tool_error(
                    "bore only supports exposing localhost ports. Use cloudflared for host!=localhost."
                )
            result = await _start_bore(port)
        elif provider == "ssh":
            if host not in ("localhost", "127.0.0.1", "::1"):
                return tool_error(
                    "ssh only supports exposing localhost ports. Use cloudflared for host!=localhost."
                )
            result = await _start_ssh_tunnel(port)
        else:
            return tool_error(f"Unknown provider: {provider}")

        logger.info("tunnel_started", port=port, provider=provider, url=result["url"])
        return tool_result(
            success=True,
            output=f"✅ Port {port} exposed at {result['url']} (via {provider})",
            data=result,
        )

    except TimeoutError as e:
        return tool_error(f"Tunnel startup timeout: {e}")
    except Exception as e:
        logger.error("tunnel_start_failed", port=port, provider=provider, error=str(e))
        return tool_error(f"Failed to start tunnel: {e}")


async def handle_stop_tunnel(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Stop an active tunnel by port number."""
    port_raw = params.get("port")
    try:
        port = int(port_raw)
    except Exception:
        port = 0
    if port <= 0 or port > 65535:
        return tool_error("port must be an integer in 1..65535")

    host = (params.get("host") or "localhost").strip() or "localhost"
    key = _tunnel_key(host, port)

    info: TunnelInfo | None = _active_tunnels.pop(key, None)
    if info is None:
        # Back-compat / operator-friendly: if host is wrong/omitted, stop the
        # only tunnel matching this port (if unique).
        matches = [k for k, v in _active_tunnels.items() if v.port == port]
        if len(matches) == 1:
            info = _active_tunnels.pop(matches[0], None)
        if info is None:
            return tool_error(f"No active tunnel on {host}:{port}")
    try:
        info.process.terminate()
        await asyncio.wait_for(info.process.wait(), timeout=5.0)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            info.process.kill()
        except ProcessLookupError:
            pass

    logger.info("tunnel_stopped", host=info.host, port=port, provider=info.provider)
    return tool_result(
        success=True,
        output=f"✅ Tunnel on {info.host}:{port} stopped (was: {info.url})",
        data={"port": port, "host": info.host, "provider": info.provider, "stopped": True},
    )


async def handle_list_tunnels(params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """List all active tunnels."""
    if not _active_tunnels:
        return tool_result(success=True, output="No active tunnels.", data={"tunnels": []})

    tunnels = []
    lines = ["Active tunnels:"]
    for _, info in _active_tunnels.items():
        entry = {
            "port": info.port,
            "host": info.host,
            "url": info.url,
            "provider": info.provider,
            "pid": info.pid,
        }
        tunnels.append(entry)
        lines.append(
            f"  • {info.host}:{info.port} → {info.url} (via {info.provider}, pid={info.pid})"
        )

    return tool_result(success=True, output="\n".join(lines), data={"tunnels": tunnels})


# ------------------------------------------------------------------
# Tool registration
# ------------------------------------------------------------------

registry.register(
    name="expose_port",
    toolset=TOOLSET,
    handler=handle_expose_port,
    description="Expose a local port to the internet via tunnel (cloudflared/bore/ssh). Returns public URL.",
    parameters={
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Local port to expose (e.g. 3000, 8080)"},
            "host": {
                "type": "string",
                "description": "Target host reachable from this process (default: localhost). Useful for Docker service names.",
                "default": "localhost",
            },
            "protocol": {"type": "string", "description": "Protocol: http or tcp (default: http)", "default": "http"},
            "subdomain": {"type": "string", "description": "Preferred subdomain (optional, not all providers support this)"},
        },
        "required": ["port"],
    },
    emoji="🚇",
    category="infrastructure",
)

registry.register(
    name="stop_tunnel",
    toolset=TOOLSET,
    handler=handle_stop_tunnel,
    description="Stop an active tunnel by port number",
    parameters={
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Port of the tunnel to stop"},
            "host": {
                "type": "string",
                "description": "Host used when creating the tunnel (default: localhost)",
                "default": "localhost",
            },
        },
        "required": ["port"],
    },
    emoji="🛑",
    category="infrastructure",
)

registry.register(
    name="list_tunnels",
    toolset=TOOLSET,
    handler=handle_list_tunnels,
    description="List all active tunnels with their public URLs",
    parameters={
        "type": "object",
        "properties": {},
    },
    emoji="📋",
    category="infrastructure",
)
