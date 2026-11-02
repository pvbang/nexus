"""
Nexus - Dev Server Manager.

Quản lý lifecycle của dev servers cho project preview:
start/stop, dynamic port allocation, health checks,
log capture, auto-restart on crash.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from app.project_builder.models import ServerInfo

logger = structlog.get_logger(__name__)

# Default log buffer capacity per server
_LOG_BUFFER_MAX_LINES = 200


@dataclass
class _ServerProcess:
    """Internal state for a running dev server process."""

    server_id: str
    process: asyncio.subprocess.Process
    info: ServerInfo
    log_buffer: list[str] = field(default_factory=list)
    started_at: float = 0.0
    _log_task: asyncio.Task[None] | None = field(
        default=None, repr=False
    )


class DevServerManager:
    """Quản lý lifecycle của dev servers cho project preview.

    Features:
        - Start/stop dev servers (vite, next, uvicorn, flask, etc.)
        - Dynamic port allocation (19000-19024 frontend, 19025-19049 backend)
        - Health check với retry
        - Log capture cho error detection
        - Auto-restart on crash

    Usage::

        manager = DevServerManager()
        info = await manager.start_server("/path/to/project", "vite")
        # info.url → "http://localhost:3100"
        await manager.stop_all()
    """

    # Port ranges
    FRONTEND_PORT_RANGE = (19000, 19024)
    BACKEND_PORT_RANGE = (19025, 19049)

    # Tech → port-range category
    _TECH_CATEGORY: dict[str, str] = {
        "vite": "frontend",
        "next": "frontend",
        "react": "frontend",
        "vue": "frontend",
        "svelte": "frontend",
        "webpack": "frontend",
        "fastapi": "backend",
        "flask": "backend",
        "express": "backend",
        "uvicorn": "backend",
        "django": "backend",
    }

    def __init__(self) -> None:
        self._servers: dict[str, _ServerProcess] = {}
        self._allocated_ports: set[int] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_server(
        self,
        project_path: str,
        tech: str,
        port: int = 0,
    ) -> ServerInfo:
        """Start dev server cho project.

        Args:
            project_path: Absolute hoặc relative path tới project dir.
            tech: Technology identifier (``vite``, ``next``, ``fastapi``,
                ``flask``, ``express``).
            port: Desired port. ``0`` → auto-allocate from range.
                Ports outside 19000-19049 are overridden to auto-allocate.

        Returns:
            :class:`ServerInfo` with url, port, server_id.

        Raises:
            RuntimeError: Nếu không thể allocate port hoặc server
                không ready sau timeout.
        """
        # 1. Validate and allocate port
        tech_type = self._TECH_CATEGORY.get(tech, "frontend")
        allowed_min = self.FRONTEND_PORT_RANGE[0]  # 19000
        allowed_max = self.BACKEND_PORT_RANGE[1]   # 19049

        if port != 0 and not (allowed_min <= port <= allowed_max):
            logger.warning(
                "dev_server_port_out_of_range",
                requested_port=port,
                allowed_range=f"{allowed_min}-{allowed_max}",
                action="auto_allocate",
            )
            port = 0  # Override to auto-allocate from valid range

        if port == 0:
            port = self.allocate_port(tech_type)
        elif port in self._allocated_ports:
            raise RuntimeError(f"Port {port} is already allocated")
        else:
            if not self._is_port_available(port):
                raise RuntimeError(f"Port {port} is already in use")
            self._allocated_ports.add(port)

        # 2. Determine command
        cmd = self._get_dev_command(tech, port)
        server_id = f"{tech}-{port}-{uuid.uuid4().hex[:8]}"

        logger.info(
            "dev_server_starting",
            server_id=server_id,
            tech=tech,
            port=port,
            project_path=project_path,
            command=" ".join(cmd),
        )

        # 3. Start subprocess
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as exc:
            self.release_port(port)
            raise RuntimeError(
                f"Failed to start dev server ({tech}): {exc}"
            ) from exc

        # 4. Build ServerInfo with both internal and external URLs
        url = f"http://localhost:{port}"
        external_url = self._resolve_external_url(port)
        info = ServerInfo(
            server_id=server_id,
            url=url,
            port=port,
            tech=tech,
            pid=process.pid or 0,
            status="starting",
            project_path=project_path,
            external_url=external_url,
        )

        sp = _ServerProcess(
            server_id=server_id,
            process=process,
            info=info,
            started_at=time.time(),
        )

        # Start background log capture
        sp._log_task = asyncio.create_task(
            self._capture_logs(sp),
            name=f"log-capture-{server_id}",
        )

        self._servers[server_id] = sp

        # 5. Open firewall port for external access
        await self._open_firewall_port(port)

        # 6. Wait for server ready
        ready = await self._wait_for_ready(url, timeout=30.0)
        if ready:
            info.status = "running"
            logger.info(
                "dev_server_ready",
                server_id=server_id,
                url=url,
                external_url=external_url,
                elapsed=round(time.time() - sp.started_at, 2),
            )
        else:
            info.status = "timeout"
            logger.warning(
                "dev_server_timeout",
                server_id=server_id,
                url=url,
            )

        return info

    async def stop_server(self, server_id: str) -> bool:
        """Stop server by ID. Kill process, release port.

        Returns:
            ``True`` if server was found and stopped.
        """
        sp = self._servers.pop(server_id, None)
        if sp is None:
            logger.warning("dev_server_not_found", server_id=server_id)
            return False

        await self._terminate_process(sp)
        self.release_port(sp.info.port)
        sp.info.status = "stopped"

        logger.info(
            "dev_server_stopped",
            server_id=server_id,
            uptime=round(time.time() - sp.started_at, 2),
        )
        return True

    async def stop_all(self) -> int:
        """Stop tất cả servers.

        Returns:
            Count of servers stopped.
        """
        server_ids = list(self._servers.keys())
        count = 0
        for sid in server_ids:
            stopped = await self.stop_server(sid)
            if stopped:
                count += 1

        logger.info("dev_servers_all_stopped", count=count)
        return count

    async def health_check(self, server_id: str) -> bool:
        """Check if server is responding via HTTP GET.

        Returns:
            ``True`` if server returns any HTTP response.
        """
        sp = self._servers.get(server_id)
        if sp is None:
            return False

        # Also check process is alive
        if sp.process.returncode is not None:
            sp.info.status = "crashed"
            return False

        return await self._http_ping(sp.info.url, timeout=3.0)

    async def get_server_logs(
        self, server_id: str, lines: int = 50
    ) -> str:
        """Lấy recent logs từ server stdout/stderr.

        Args:
            server_id: ID of the server.
            lines: Number of most-recent lines to return.

        Returns:
            Multi-line string of recent log output.
        """
        sp = self._servers.get(server_id)
        if sp is None:
            return ""
        recent = sp.log_buffer[-lines:]
        return "\n".join(recent)

    async def restart_server(self, server_id: str) -> ServerInfo:
        """Stop rồi start lại server.

        Raises:
            RuntimeError: If server not found or restart fails.
        """
        sp = self._servers.get(server_id)
        if sp is None:
            raise RuntimeError(f"Server {server_id} not found")

        project_path = sp.info.project_path
        tech = sp.info.tech
        port = sp.info.port

        logger.info("dev_server_restarting", server_id=server_id)

        await self.stop_server(server_id)

        return await self.start_server(project_path, tech, port)

    def get_server(self, server_id: str) -> ServerInfo | None:
        """Lấy info của server, hoặc ``None`` nếu không tồn tại."""
        sp = self._servers.get(server_id)
        return sp.info if sp else None

    def list_servers(self) -> list[ServerInfo]:
        """List tất cả active servers."""
        return [sp.info for sp in self._servers.values()]

    # ------------------------------------------------------------------
    # Port management
    # ------------------------------------------------------------------

    def allocate_port(self, tech_type: str = "frontend") -> int:
        """Allocate an available port from the appropriate range.

        Args:
            tech_type: ``"frontend"`` (19000-19024) or ``"backend"``
                (19025-19049).

        Returns:
            An available port number.

        Raises:
            RuntimeError: If the entire port range is exhausted.
        """
        if tech_type == "backend":
            port_range = self.BACKEND_PORT_RANGE
        else:
            port_range = self.FRONTEND_PORT_RANGE

        for port in range(port_range[0], port_range[1] + 1):
            if port in self._allocated_ports:
                continue
            if self._is_port_available(port):
                self._allocated_ports.add(port)
                logger.debug(
                    "port_allocated",
                    port=port,
                    tech_type=tech_type,
                )
                return port

        raise RuntimeError(
            f"No available ports in range {port_range[0]}-{port_range[1]} "
            f"for {tech_type}"
        )

    def release_port(self, port: int) -> None:
        """Release port back to pool."""
        self._allocated_ports.discard(port)
        logger.debug("port_released", port=port)

    # ------------------------------------------------------------------
    # Ready / health check helpers
    # ------------------------------------------------------------------

    async def _wait_for_ready(
        self, url: str, timeout: float = 30.0
    ) -> bool:
        """Poll URL until server responds or timeout.

        Polls every 0.5s. Returns ``True`` if server responded
        within timeout.
        """
        deadline = time.time() + timeout
        poll_interval = 0.5

        while time.time() < deadline:
            if await self._http_ping(url, timeout=2.0):
                return True
            await asyncio.sleep(poll_interval)

        return False

    @staticmethod
    async def _http_ping(url: str, timeout: float = 2.0) -> bool:
        """HTTP GET ping. Returns True if any response received.

        Uses ``asyncio.open_connection`` to avoid external dependency
        on aiohttp. Falls back to basic TCP connect if HTTP parse fails.
        """
        try:
            # Parse host/port from url
            # url format: http://localhost:PORT or http://localhost:PORT/path
            stripped = url.replace("http://", "").replace("https://", "")
            host_port = stripped.split("/")[0]
            if ":" in host_port:
                host, port_str = host_port.rsplit(":", 1)
                port = int(port_str)
            else:
                host = host_port
                port = 80

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )

            # Send minimal HTTP GET
            request_line = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Connection: close\r\n\r\n"
            )
            writer.write(request_line.encode())
            await writer.drain()

            # Read first line of response
            response_line = await asyncio.wait_for(
                reader.readline(),
                timeout=timeout,
            )
            writer.close()
            # Suppress the error on close if transport is already gone
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass

            # Any HTTP response means server is up
            return response_line.startswith(b"HTTP/")

        except (
            OSError,
            asyncio.TimeoutError,
            ConnectionRefusedError,
            ConnectionResetError,
            ValueError,
        ):
            return False

    # ------------------------------------------------------------------
    # Dev command mapping
    # ------------------------------------------------------------------

    @staticmethod
    def _get_dev_command(tech: str, port: int) -> list[str]:
        """Map tech identifier → dev server command list.

        Args:
            tech: Technology name.
            port: Port to bind.

        Returns:
            Command as list of strings for ``create_subprocess_exec``.
        """
        port_s = str(port)
        commands: dict[str, list[str]] = {
            "vite": ["npx", "vite", "--port", port_s, "--host"],
            "react": ["npx", "vite", "--port", port_s, "--host"],
            "next": ["npx", "next", "dev", "--port", port_s, "--hostname", "0.0.0.0"],
            "vue": ["npx", "vite", "--port", port_s, "--host"],
            "svelte": [
                "npx",
                "vite",
                "--port",
                port_s,
                "--host",
            ],
            "webpack": [
                "npx",
                "webpack",
                "serve",
                "--port",
                port_s,
                "--host",
                "0.0.0.0",
            ],
            "fastapi": [
                "uvicorn",
                "app.main:app",
                "--reload",
                "--host",
                "0.0.0.0",
                "--port",
                port_s,
            ],
            "uvicorn": [
                "uvicorn",
                "app.main:app",
                "--reload",
                "--host",
                "0.0.0.0",
                "--port",
                port_s,
            ],
            "flask": [
                "flask",
                "run",
                "--host",
                "0.0.0.0",
                "--port",
                port_s,
            ],
            "express": ["node", "server.js"],
            "django": [
                "python",
                "manage.py",
                "runserver",
                f"0.0.0.0:{port_s}",
            ],
        }

        cmd = commands.get(tech)
        if cmd is None:
            logger.warning(
                "dev_command_unknown_tech",
                tech=tech,
                fallback="npx vite",
            )
            cmd = ["npx", "vite", "--port", port_s, "--host"]

        return cmd

    # ------------------------------------------------------------------
    # Process management helpers
    # ------------------------------------------------------------------

    async def _terminate_process(self, sp: _ServerProcess) -> None:
        """Gracefully terminate process, then force-kill if needed."""
        # Cancel log capture task
        if sp._log_task and not sp._log_task.done():
            sp._log_task.cancel()
            try:
                await sp._log_task
            except asyncio.CancelledError:
                pass

        proc = sp.process
        if proc.returncode is not None:
            # Already exited
            return

        # Try graceful terminate first
        try:
            proc.terminate()
        except ProcessLookupError:
            return

        # Wait up to 5s for graceful shutdown
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            # Force kill
            logger.warning(
                "dev_server_force_kill",
                server_id=sp.server_id,
                pid=proc.pid,
            )
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                pass

    async def _capture_logs(self, sp: _ServerProcess) -> None:
        """Background task: read stdout+stderr and store in log_buffer.

        Runs until the process exits or the task is cancelled.
        """
        async def _read_stream(
            stream: asyncio.StreamReader | None, prefix: str
        ) -> None:
            if stream is None:
                return
            try:
                while True:
                    line_bytes = await stream.readline()
                    if not line_bytes:
                        break
                    line = line_bytes.decode("utf-8", errors="replace").rstrip()
                    sp.log_buffer.append(f"[{prefix}] {line}")
                    # Trim buffer
                    if len(sp.log_buffer) > _LOG_BUFFER_MAX_LINES:
                        sp.log_buffer[:] = sp.log_buffer[
                            -_LOG_BUFFER_MAX_LINES:
                        ]
            except asyncio.CancelledError:
                raise
            except Exception:
                pass

        try:
            await asyncio.gather(
                _read_stream(sp.process.stdout, "out"),
                _read_stream(sp.process.stderr, "err"),
            )
        except asyncio.CancelledError:
            return

        # Process exited — update status
        if sp.server_id in self._servers:
            sp.info.status = "exited"
            logger.info(
                "dev_server_process_exited",
                server_id=sp.server_id,
                return_code=sp.process.returncode,
            )

    # ------------------------------------------------------------------
    # External URL resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_external_url(port: int) -> str:
        """Resolve external URL that users outside can access.

        Thin wrapper: gathers config + detected IP + Docker flag, then delegates
        the (pure) decision to ``resolve_external_url``. See that function for the
        priority order and why we no longer fabricate a Docker-internal IP.
        """
        from app.config import get_settings
        from app.project_builder.url_resolver import resolve_external_url

        settings = get_settings()
        in_docker = (
            Path("/.dockerenv").exists()
            or os.environ.get("RUNNING_IN_DOCKER") == "1"
        )
        detected_ip = DevServerManager._detect_external_ip()

        url, hint = resolve_external_url(
            port=port,
            external_base_url=settings.external_base_url,
            public_host=settings.public_host,
            detected_ip=detected_ip,
            in_docker=in_docker,
        )
        if hint:
            logger.warning("docker_no_public_host", detected_ip=detected_ip, hint=hint)
        return url

    @staticmethod
    def _detect_external_ip() -> str:
        """Detect the machine's external-facing IP address.

        Uses UDP socket trick (no actual data sent) to determine
        which network interface would route to the internet.
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                # Connect to public DNS — doesn't send data, just resolves route
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                if not ip.startswith("127."):
                    return ip
        except (OSError, socket.error):
            pass

        # Fallback: hostname resolution
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            if not ip.startswith("127."):
                return ip
        except socket.error:
            pass

        return "localhost"

    # ------------------------------------------------------------------
    # Firewall port management
    # ------------------------------------------------------------------

    @staticmethod
    async def _open_firewall_port(port: int) -> None:
        """Attempt to open port on firewall for external access.

        Tries ufw first, then iptables. Fails silently if neither
        is available or if lacking permissions (non-root).
        """
        from app.config import get_settings
        settings = get_settings()

        if not settings.auto_open_firewall_port:
            return

        # Try ufw (Ubuntu/Debian)
        try:
            proc = await asyncio.create_subprocess_exec(
                "ufw", "allow", str(port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            if proc.returncode == 0:
                logger.info("firewall_port_opened_ufw", port=port)
                return
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            pass

        # Try iptables (generic Linux)
        try:
            proc = await asyncio.create_subprocess_exec(
                "iptables", "-I", "INPUT", "-p", "tcp",
                "--dport", str(port), "-j", "ACCEPT",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            if proc.returncode == 0:
                logger.info("firewall_port_opened_iptables", port=port)
                return
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            pass

        logger.debug(
            "firewall_port_open_skipped",
            port=port,
            reason="no ufw/iptables or insufficient permissions",
        )

    # ------------------------------------------------------------------
    # Port availability check
    # ------------------------------------------------------------------

    @staticmethod
    def _is_port_available(port: int) -> bool:
        """Check if port is free using socket bind test."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
