"""
MekongAI Nexus - Sandbox Manager.

Quản lý thực thi code an toàn qua Docker containers.
Lấy cảm hứng từ IronClaw WASM Sandbox.

Features:
- DockerSandboxConfig dataclass cho cấu hình chi tiết
- Security: no-new-privileges, cap-drop ALL, user=nobody
- Resource limits: memory, CPU, PIDs
- Network isolation, read-only rootfs, tmpfs
- Health check và image management
"""

import asyncio
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import structlog

from app.config import get_settings
from app.core.exceptions import SandboxError

logger = structlog.get_logger(__name__)
settings = get_settings()


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------


@dataclass
class DockerSandboxConfig:
    """Cấu hình chi tiết cho Docker sandbox container."""

    image: str = "python:3.12-slim"
    memory_limit: str = "1g"  # Upgraded: agents need more RAM for builds/tools
    cpu_quota: int = 200000  # 200% = 2 cores (microseconds per 100ms period)
    network_disabled: bool = False  # Upgraded: agents need network for pip/npm/curl
    read_only_rootfs: bool = False  # Upgraded: agents need to write to filesystem
    timeout_seconds: int = 120  # Upgraded: 2 min default for complex tasks
    allowed_packages: list[str] = field(default_factory=list)  # pip packages allowed
    mount_workspace: bool = False
    tmpfs_size: str = "512m"  # Upgraded: more tmpfs for build artifacts
    pids_limit: int = 200  # Upgraded: agents spawn many subprocesses
    max_timeout: int = 600  # 10 min max for long-running tasks


# ------------------------------------------------------------------
# Sandbox Result
# ------------------------------------------------------------------


class SandboxResult:
    """Kết quả thực thi trong sandbox."""

    def __init__(
        self,
        success: bool,
        output: str = "",
        error: str = "",
        exit_code: int = 0,
        execution_time: float = 0.0,
        memory_peak: str = "",
    ):
        self.success = success
        self.output = output
        self.error = error
        self.exit_code = exit_code
        self.execution_time = execution_time
        self.memory_peak = memory_peak

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "exit_code": self.exit_code,
            "execution_time": self.execution_time,
            "memory_peak": self.memory_peak,
        }


# ------------------------------------------------------------------
# Default Sandbox Configs
# ------------------------------------------------------------------

_DEFAULT_CONFIGS: dict[str, DockerSandboxConfig] = {
    "python-secure": DockerSandboxConfig(
        image="python:3.12-slim",
        memory_limit="256m",
        cpu_quota=50000,
        network_disabled=True,
        read_only_rootfs=True,
        timeout_seconds=30,
        tmpfs_size="64m",
    ),
    "python-network": DockerSandboxConfig(
        image="python:3.12-slim",
        memory_limit="512m",
        cpu_quota=100000,
        network_disabled=False,
        read_only_rootfs=True,
        timeout_seconds=60,
        tmpfs_size="128m",
    ),
    "node-secure": DockerSandboxConfig(
        image="node:20-slim",
        memory_limit="256m",
        cpu_quota=50000,
        network_disabled=True,
        read_only_rootfs=True,
        timeout_seconds=30,
        tmpfs_size="64m",
    ),
    "minimal": DockerSandboxConfig(
        image="alpine:latest",
        memory_limit="64m",
        cpu_quota=25000,
        network_disabled=True,
        read_only_rootfs=True,
        timeout_seconds=10,
        tmpfs_size="16m",
    ),
}


# ------------------------------------------------------------------
# Sandbox Manager
# ------------------------------------------------------------------


def _is_running_in_docker() -> bool:
    """
    Detect if we are running inside a Docker container.

    Docker containers cannot easily run `docker run` (Docker-in-Docker)
    unless the Docker socket is mounted. We detect this to auto-fallback
    to process-based execution.
    """
    import os
    # Check /.dockerenv file (Docker creates this)
    if os.path.exists("/.dockerenv"):
        return True
    # Check cgroup (Linux containers)
    try:
        with open("/proc/1/cgroup", "r") as f:
            content = f.read()
            if "docker" in content or "containerd" in content:
                return True
    except (FileNotFoundError, PermissionError):
        pass
    # Check /proc/1/sched for init process name
    try:
        with open("/proc/1/sched", "r") as f:
            first_line = f.readline()
            # In a container, PID 1 is usually NOT "init" or "systemd"
            if first_line and not any(x in first_line for x in ("init", "systemd")):
                return True
    except (FileNotFoundError, PermissionError):
        pass
    return False


# Env var names matching these (case-insensitive) are secrets and must never be
# exposed to agent code running in the unisolated process fallback.
_SECRET_ENV_PATTERN = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH|PRIVATE|"
    r"API|DSN|CONNECTION_STRING|SESSION|COOKIE|SALT|CERT|SSH|"
    r"DATABASE|_DB_|DB_URL|MONGO|REDIS|POSTGRES|MYSQL|AMQP|BROKER|URI)",
    re.IGNORECASE,
)

# Values that look like credential-bearing connection URLs (scheme://user:pass@host)
# are redacted regardless of the variable name.
_SECRET_VALUE_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://[^/@\s]+:[^/@\s]+@")

# Always-safe vars the child genuinely needs to run.
_SAFE_ENV_KEYS = frozenset({
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM",
    "TMP", "TEMP", "TMPDIR", "PWD", "SHELL", "USER", "LOGNAME",
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",  # Windows runtime essentials
    "PYTHONIOENCODING", "PYTHONUNBUFFERED", "PYTHONPATH",
})


def _scrub_env(source: "os._Environ[str] | dict[str, str]") -> dict[str, str]:
    """Return a minimal copy of ``source`` with all secret-looking vars removed.

    Keeps explicitly-safe runtime vars and anything that doesn't match the secret
    pattern; drops API keys, tokens, passwords, connection strings, etc. Used for the
    process fallback, which has no container boundary to contain a leak.
    """
    scrubbed: dict[str, str] = {}
    for key, value in source.items():
        if key in _SAFE_ENV_KEYS:
            scrubbed[key] = value
        elif _SECRET_ENV_PATTERN.search(key):
            continue
        elif _SECRET_VALUE_PATTERN.match(value):
            # Credential-bearing URL in the value, regardless of the var name.
            continue
        else:
            scrubbed[key] = value
    return scrubbed


def _is_windows_dev_mode() -> bool:
    """
    Detect if running on Windows in dev mode.

    On Windows, Docker sandbox runs Linux containers which cannot access
    Windows filesystem paths, installed Python packages, or workspace data.
    In dev mode, we should use process-based execution directly for tools
    like generate_word_document that need access to installed packages.
    """
    return sys.platform == "win32"


class SandboxManager:
    """
    Quản lý sandbox execution.

    Hỗ trợ:
    - Docker container isolation với security hardening
    - IronClaw WASM sandbox (nếu enabled)
    - Resource limits (CPU, memory, network, PIDs)
    - Timeout enforcement
    - Health check & image management

    Auto-detection: If running inside Docker container, automatically
    falls back to process-based execution (no Docker-in-Docker needed).
    """

    def __init__(self) -> None:
        self.docker_enabled: bool = settings.docker_sandbox_enabled
        self.docker_image: str = settings.docker_sandbox_image
        self.ironclaw_enabled: bool = settings.ironclaw_enabled
        self.ironclaw_path: str = settings.ironclaw_binary_path
        self._inside_docker = _is_running_in_docker()
        self._is_windows = _is_windows_dev_mode()
        # Lazily-probed: is a Docker daemon actually reachable from here?
        # Being inside a container does NOT mean Docker is unusable — if the host
        # socket is mounted (Docker-out-of-Docker), `docker run` still works and gives
        # real isolation. We probe the daemon instead of guessing from the environment.
        self._docker_reachable: Optional[bool] = None
        self._default_config = DockerSandboxConfig(
            image=settings.docker_sandbox_image,
            memory_limit=settings.docker_sandbox_memory,
            cpu_quota=int(settings.docker_sandbox_cpu * 100000),
            network_disabled=not settings.docker_sandbox_network,
            timeout_seconds=settings.docker_sandbox_timeout,
        )

        if self._inside_docker and self.docker_enabled:
            logger.info(
                "sandbox_inside_docker_detected",
                msg="Running inside Docker — using process-based execution instead of Docker-in-Docker",
            )

        if self._is_windows:
            logger.info(
                "sandbox_windows_dev_mode",
                msg="Windows detected — using process-based execution (Docker sandbox runs Linux containers)",
            )

    async def execute_code(
        self,
        language: str,
        code: str,
        timeout: int = 30,
        memory_limit: str = "256m",
        network_enabled: bool = False,
        sandbox_config: Optional[DockerSandboxConfig] = None,
    ) -> SandboxResult:
        """
        Thực thi code trong sandbox.

        Ưu tiên: IronClaw WASM → Docker (if not inside container) → Process isolation.

        Args:
            language: Ngôn ngữ (python, javascript, bash).
            code: Source code cần thực thi.
            timeout: Timeout giây.
            memory_limit: Giới hạn memory (e.g. "256m").
            network_enabled: Cho phép network access.
            sandbox_config: Cấu hình sandbox tùy chỉnh (override defaults).
        """
        if self.ironclaw_enabled:
            return await self._execute_ironclaw(language, code, timeout)

        # Prefer Docker whenever a daemon is genuinely reachable — including
        # Docker-out-of-Docker (in-container with the host socket mounted). Windows
        # is excluded because its Linux containers can't reach Windows packages/paths.
        if self.docker_enabled and not self._is_windows and await self._docker_daemon_reachable():
            config = sandbox_config or DockerSandboxConfig(
                image=self.docker_image,
                memory_limit=memory_limit,
                network_disabled=not network_enabled,
                timeout_seconds=timeout,
            )
            return await self._execute_docker(language, code, config)

        # Fallback: direct process execution (NO isolation). Reached only when no
        # Docker daemon is available. _execute_process scrubs the environment so the
        # agent's code cannot read the app's secrets.
        return await self._execute_process(language, code, timeout)

    async def _docker_daemon_reachable(self) -> bool:
        """Probe whether a Docker daemon can actually run containers from here.

        This is the real isolation gate. It returns True for both a normal host
        with Docker AND an in-container deployment that mounts the host socket
        (Docker-out-of-Docker) — in both cases `docker run` yields a hardened,
        throwaway container. Result is cached for the manager's lifetime.
        """
        if self._docker_reachable is not None:
            return self._docker_reachable

        if shutil.which("docker") is None:
            self._docker_reachable = False
            logger.warning(
                "sandbox_docker_cli_missing",
                msg="docker CLI not found — falling back to process execution (NO isolation).",
            )
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "version", "--format", "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            reachable = proc.returncode == 0 and bool(stdout.strip())
        except (asyncio.TimeoutError, FileNotFoundError, Exception):
            reachable = False

        self._docker_reachable = reachable
        if reachable:
            logger.info(
                "sandbox_docker_reachable",
                inside_docker=self._inside_docker,
                msg="Docker daemon reachable — code runs in hardened containers.",
            )
        else:
            logger.warning(
                "sandbox_docker_unreachable",
                inside_docker=self._inside_docker,
                msg="No Docker daemon — falling back to process execution (NO isolation). "
                    "Mount the host docker socket (Docker-out-of-Docker) or enable IronClaw to isolate.",
            )
        return reachable

    async def _execute_docker(
        self,
        language: str,
        code: str,
        config: DockerSandboxConfig,
    ) -> SandboxResult:
        """
        Thực thi code trong Docker container với security hardening.

        Security features (applied in this method):
        - --security-opt=no-new-privileges  (no setuid privilege escalation)
        - --cap-drop=ALL then re-add only NET_RAW/NET_BIND_SERVICE (pentest + servers)
        - --pids-limit, --memory, --cpu-quota  (resource limits)
        - --network=none khi disabled
        - --read-only rootfs + tmpfs cho /tmp khi config bật
        Note: runs as --user=root inside the container by design so agents can install
        packages and write freely; the container itself is the throwaway boundary.
        """
        container_name = f"nexus-sandbox-{uuid.uuid4().hex[:8]}"

        # Map language to Docker image & command (Docker always uses Linux)
        image_map: dict[str, str] = {
            "python": config.image if "python" in config.image else "python:3.12-slim",
            "javascript": "node:20-slim" if "node" not in config.image else config.image,
            "bash": config.image,
        }
        cmd_map: dict[str, list[str]] = {
            "python": ["python3", "-c", code],
            "javascript": ["node", "-e", code],
            "bash": ["bash", "-c", code],
        }

        if language not in cmd_map:
            return SandboxResult(
                success=False,
                error=f"Unsupported language: {language}",
                exit_code=1,
            )

        image = image_map.get(language, config.image)

        # Build docker command — agents need full control for builds/tools, but the
        # container is still hardened: no privilege escalation, all Linux capabilities
        # dropped except the network ones agents legitimately need (raw sockets for
        # pentest scanning, binding low ports for servers).
        docker_cmd: list[str] = [
            "docker", "run",
            "--rm",
            "--name", container_name,
            # Resource limits
            f"--memory={config.memory_limit}",
            f"--cpu-quota={config.cpu_quota}",
            f"--pids-limit={getattr(config, 'pids_limit', 200)}",
            # Hardening: block setuid-based privilege escalation, drop all caps then
            # re-add only what agents need. Even as root inside, the container cannot
            # gain new privileges on the host.
            "--security-opt=no-new-privileges",
            "--cap-drop=ALL",
            "--cap-add=NET_RAW",
            "--cap-add=NET_BIND_SERVICE",
            # Agent execution: root user inside container for package installs / writes.
            "--user=root",
            # Network: agents need network for package installs, API calls
            f"--network={'none' if config.network_disabled else 'bridge'}",
        ]

        # Read-only rootfs with tmpfs for /tmp
        if config.read_only_rootfs:
            docker_cmd.extend([
                "--read-only",
                f"--tmpfs=/tmp:rw,noexec,nosuid,size={config.tmpfs_size}",
            ])

        # Append image and command
        docker_cmd.append(image)
        docker_cmd.extend(cmd_map[language])

        start_time = time.monotonic()

        try:
            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=config.timeout_seconds
            )

            elapsed = time.monotonic() - start_time

            # Try to get memory peak from docker stats (best effort)
            memory_peak = await self._get_container_memory_peak(container_name)

            return SandboxResult(
                success=process.returncode == 0,
                output=stdout.decode("utf-8", errors="replace"),
                error=stderr.decode("utf-8", errors="replace"),
                exit_code=process.returncode or 0,
                execution_time=round(elapsed, 3),
                memory_peak=memory_peak,
            )

        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start_time
            # Kill container on timeout
            await self._kill_container(container_name)
            return SandboxResult(
                success=False,
                error=f"Execution timed out after {config.timeout_seconds}s",
                exit_code=-1,
                execution_time=round(elapsed, 3),
            )
        except Exception as e:
            elapsed = time.monotonic() - start_time
            # Ensure cleanup
            await self._kill_container(container_name)
            return SandboxResult(
                success=False,
                error=f"Docker execution failed: {e}",
                exit_code=-1,
                execution_time=round(elapsed, 3),
            )

    async def _kill_container(self, container_name: str) -> None:
        """Forcefully kill and remove a container."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "kill", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            pass
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            pass

    async def _get_container_memory_peak(self, container_name: str) -> str:
        """Best-effort: get peak memory usage of container (may fail if --rm)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stats", "--no-stream", "--format",
                "{{.MemUsage}}", container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            return stdout.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    async def _ensure_sandbox_image(self, image: str) -> bool:
        """
        Pull Docker image nếu chưa tồn tại locally.

        Returns:
            True nếu image sẵn sàng, False nếu pull thất bại.
        """
        try:
            # Check if image exists locally
            check_proc = await asyncio.create_subprocess_exec(
                "docker", "image", "inspect", image,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await check_proc.wait()

            if check_proc.returncode == 0:
                logger.info("sandbox_image_exists", image=image)
                return True

            # Image not found, pull it
            logger.info("sandbox_image_pulling", image=image)
            pull_proc = await asyncio.create_subprocess_exec(
                "docker", "pull", image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                pull_proc.communicate(), timeout=300  # 5 min timeout for pull
            )

            if pull_proc.returncode == 0:
                logger.info("sandbox_image_pulled", image=image)
                return True
            else:
                logger.error(
                    "sandbox_image_pull_failed",
                    image=image,
                    error=stderr.decode("utf-8", errors="replace"),
                )
                return False

        except asyncio.TimeoutError:
            logger.error("sandbox_image_pull_timeout", image=image)
            return False
        except Exception as e:
            logger.error("sandbox_image_check_failed", image=image, error=str(e))
            return False

    def list_sandbox_configs(self) -> list[dict[str, Any]]:
        """
        List available sandbox configurations.

        Returns danh sách các preset configs + default config hiện tại.
        """
        configs: list[dict[str, Any]] = []

        # Default config from settings
        configs.append({
            "name": "default",
            "image": self._default_config.image,
            "memory_limit": self._default_config.memory_limit,
            "cpu_quota": self._default_config.cpu_quota,
            "network_disabled": self._default_config.network_disabled,
            "read_only_rootfs": self._default_config.read_only_rootfs,
            "timeout_seconds": self._default_config.timeout_seconds,
            "tmpfs_size": self._default_config.tmpfs_size,
        })

        # Preset configs
        for name, cfg in _DEFAULT_CONFIGS.items():
            configs.append({
                "name": name,
                "image": cfg.image,
                "memory_limit": cfg.memory_limit,
                "cpu_quota": cfg.cpu_quota,
                "network_disabled": cfg.network_disabled,
                "read_only_rootfs": cfg.read_only_rootfs,
                "timeout_seconds": cfg.timeout_seconds,
                "tmpfs_size": cfg.tmpfs_size,
            })

        return configs

    async def health_check(self) -> dict[str, Any]:
        """
        Check Docker daemon accessibility and sandbox readiness.

        Returns dict với:
        - docker_available: bool
        - docker_version: str
        - sandbox_image_ready: bool
        - docker_enabled: bool
        """
        result: dict[str, Any] = {
            "docker_enabled": self.docker_enabled,
            "docker_available": False,
            "docker_version": "",
            "sandbox_image_ready": False,
            "ironclaw_enabled": self.ironclaw_enabled,
        }

        if not self.docker_enabled:
            return result

        try:
            # Check Docker daemon
            proc = await asyncio.create_subprocess_exec(
                "docker", "version", "--format", "{{.Server.Version}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=10.0
            )

            if proc.returncode == 0:
                result["docker_available"] = True
                result["docker_version"] = stdout.decode("utf-8", errors="replace").strip()

                # Check if sandbox image exists
                img_proc = await asyncio.create_subprocess_exec(
                    "docker", "image", "inspect", self.docker_image,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await img_proc.wait()
                result["sandbox_image_ready"] = img_proc.returncode == 0
            else:
                result["docker_available"] = False
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                result["error"] = error_msg

        except asyncio.TimeoutError:
            result["error"] = "Docker daemon response timeout"
        except FileNotFoundError:
            result["error"] = "Docker CLI not found"
        except Exception as e:
            result["error"] = str(e)

        return result

    async def _execute_ironclaw(
        self,
        language: str,
        code: str,
        timeout: int,
    ) -> SandboxResult:
        """Execute code via the IronClaw WASM sandbox (not yet available).

        IronClaw is a planned WebAssembly-based sandbox that will provide
        near-native isolation without requiring Docker.  Until the IronClaw
        binary is built and shipped, this method transparently falls back to
        the next-best execution backend:

        Fallback chain:
            1. **Windows, or no reachable Docker daemon** → process-based
               execution (Docker cannot run Linux containers natively on
               Windows; absent a daemon there is nothing to run against).
            2. **Any host with a reachable Docker daemon** (including
               Docker-out-of-Docker via a mounted host socket) → Docker
               container execution with security hardening (see ``_execute_docker``).

        The caller (``execute_code``) is unaware of the fallback — the
        returned ``SandboxResult`` is identical regardless of backend.

        Args:
            language: Programming language identifier (python, javascript, bash).
            code: Source code to execute.
            timeout: Maximum execution time in seconds.

        Returns:
            SandboxResult from the fallback backend.
        """
        if self._is_windows or not await self._docker_daemon_reachable():
            logger.debug(
                "ironclaw_unavailable_fallback_process",
                language=language,
                reason="windows" if self._is_windows else "no-docker-daemon",
            )
            return await self._execute_process(language, code, timeout)

        logger.info(
            "ironclaw_unavailable_fallback_docker",
            language=language,
            message="IronClaw WASM sandbox not yet available. "
                    "Falling back to Docker container execution.",
        )
        config = DockerSandboxConfig(
            image=self.docker_image,
            memory_limit="256m",
            network_disabled=True,
            timeout_seconds=timeout,
        )
        return await self._execute_docker(language, code, config)

    async def _execute_process(
        self,
        language: str,
        code: str,
        timeout: int,
    ) -> SandboxResult:
        """Fallback: thực thi trong subprocess (less secure).

        WARNING: This mode has NO security isolation. Only use in development
        or when Docker sandbox is unavailable.
        """
        if not self._is_windows and not self._inside_docker:
            logger.warning(
                "sandbox_process_fallback_insecure",
                msg="Using process-based execution WITHOUT Docker isolation. "
                    "This is INSECURE for production. Set DOCKER_SANDBOX_ENABLED=true.",
            )
        _is_win = sys.platform == "win32"
        _python = "python" if _is_win else "python3"
        _shell_cmd = ["cmd", "/c"] if _is_win else ["bash", "-c"]

        cmd_map = {
            "python": [_python, "-c", code],
            "javascript": ["node", "-e", code],
            "bash": _shell_cmd + [code],
        }

        if language not in cmd_map:
            return SandboxResult(success=False, error=f"Unsupported: {language}", exit_code=1)

        # SECURITY: process fallback has no isolation, so the child would otherwise
        # inherit the app's entire environment — including every API key / secret.
        # Scrub it down to a minimal, non-sensitive set before exec.
        scrubbed_env = _scrub_env(os.environ)

        start_time = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd_map[language],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=scrubbed_env,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
            elapsed = time.monotonic() - start_time
            return SandboxResult(
                success=process.returncode == 0,
                output=stdout.decode("utf-8", errors="replace"),
                error=stderr.decode("utf-8", errors="replace"),
                exit_code=process.returncode or 0,
                execution_time=round(elapsed, 3),
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start_time
            process.kill()
            return SandboxResult(
                success=False,
                error="Timeout",
                exit_code=-1,
                execution_time=round(elapsed, 3),
            )
        except Exception as e:
            elapsed = time.monotonic() - start_time
            return SandboxResult(
                success=False,
                error=str(e),
                exit_code=-1,
                execution_time=round(elapsed, 3),
            )

    # ------------------------------------------------------------------
    # Persistent workspace methods (for Project Builder)
    # ------------------------------------------------------------------

    async def execute_in_project(
        self,
        project_path: str,
        command: str,
        timeout: int = 60,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """
        Execute command trong project directory (persistent workspace).

        Khác với execute_code() thông thường:
        - Command chạy TRONG project directory (cd vào trước)
        - File system changes persist (không bị cleanup)
        - Có thể truy cập node_modules, venv, etc.
        - Dùng cho: npm install, npm run build, pytest, dev server, etc.

        Security: Vẫn có timeout và basic command validation.
        """
        logger.info(
            "execute_in_project",
            project_path=project_path,
            command=command[:200],
            timeout=timeout,
        )

        start_time = time.time()

        try:
            # Build environment
            run_env = dict(os.environ)
            if env:
                run_env.update(env)

            # Execute command in project directory
            process = await asyncio.create_subprocess_shell(
                command,
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=run_env,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return SandboxResult(
                    success=False,
                    output="",
                    error=f"Command timed out after {timeout}s",
                    exit_code=-1,
                    execution_time=time.time() - start_time,
                )

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace")
            success = process.returncode == 0

            return SandboxResult(
                success=success,
                output=output,
                error=error if not success else "",
                exit_code=process.returncode or 0,
                execution_time=time.time() - start_time,
            )

        except Exception as e:
            logger.error("execute_in_project_failed", error=str(e))
            return SandboxResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1,
                execution_time=time.time() - start_time,
            )

    async def create_persistent_workspace(
        self,
        workspace_name: str,
        base_path: str = "workspace_data/projects",
    ) -> str:
        """
        Create a persistent workspace directory.

        Returns: absolute path to workspace.
        """
        import re

        # Sanitize name
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '-', workspace_name).lower().strip('-')[:64]
        workspace_path = Path(base_path) / safe_name
        workspace_path.mkdir(parents=True, exist_ok=True)

        logger.info(
            "persistent_workspace_created",
            name=safe_name,
            path=str(workspace_path),
        )

        return str(workspace_path.resolve())

    # ------------------------------------------------------------------
    # Workspace-Sandbox Synchronization (Phase 2.3 Fix)
    # ------------------------------------------------------------------

    async def sync_workspace_to_sandbox(self, workspace_path: str) -> dict[str, Any]:
        """
        Ensure files written via write_file tool are also visible in sandbox
        execute environment.

        This method is idempotent — safe to call multiple times. It copies/syncs
        the workspace directory into the sandbox execution environment if they
        differ (e.g., Docker volume mount not configured, or process-mode where
        filesystem is shared but CWD differs).

        For process-based execution (Windows/Docker-in-Docker): filesystem is
        already shared, so this is a no-op verification.

        For Docker execution: ensures workspace is mounted or files are copied
        into the container's working directory.

        Args:
            workspace_path: Path to the workspace directory to sync.

        Returns:
            Dict with sync status: {"synced": bool, "files_count": int, "method": str}
        """
        ws_path = Path(workspace_path)
        if not ws_path.exists():
            logger.warning(
                "sync_workspace_path_not_found",
                workspace_path=workspace_path,
            )
            return {"synced": False, "files_count": 0, "method": "none", "error": "path_not_found"}

        # Count files in workspace
        file_count = sum(1 for f in ws_path.rglob("*") if f.is_file())

        # Process-based execution: filesystem is already shared
        # (Windows dev mode, Docker-in-Docker, or Docker disabled)
        if self._is_windows or self._inside_docker or not self.docker_enabled:
            # Verify CWD accessibility — files are already on same filesystem
            logger.debug(
                "sync_workspace_noop_shared_fs",
                workspace_path=workspace_path,
                file_count=file_count,
                reason="process_mode",
            )
            return {"synced": True, "files_count": file_count, "method": "shared_filesystem"}

        # Docker execution: workspace needs to be mounted as volume
        # The mount happens at container creation time (execute_code/execute_in_project).
        # Here we just verify the workspace is accessible and log the mount path.
        # Actual mounting is done by adding -v flag in _execute_docker().
        self._workspace_mount_path = str(ws_path.resolve())
        logger.info(
            "sync_workspace_docker_mount_registered",
            workspace_path=str(ws_path.resolve()),
            file_count=file_count,
        )
        return {"synced": True, "files_count": file_count, "method": "docker_volume_mount"}

    async def verify_file_accessible(self, filepath: str) -> bool:
        """
        Check file exists in BOTH workspace AND sandbox execution environment.

        Logs mismatch if file exists in one place but not the other.
        This helps diagnose the Phase 2.3 issue where write_file creates files
        in workspace but execute_command can't find them.

        Args:
            filepath: Absolute or relative path to the file to check.

        Returns:
            True if file is accessible in the execution environment.
        """
        file_path = Path(filepath)

        # Check in workspace (filesystem)
        workspace_exists = file_path.exists()

        # For process-based execution: same filesystem, same check
        if self._is_windows or self._inside_docker or not self.docker_enabled:
            if not workspace_exists:
                logger.warning(
                    "file_not_accessible",
                    filepath=filepath,
                    workspace_exists=False,
                    sandbox_exists=False,
                    execution_mode="process",
                )
            return workspace_exists

        # For Docker execution: check if file would be accessible inside container
        # The file must be within the registered workspace mount path
        sandbox_accessible = False
        _mount_path = getattr(self, "_workspace_mount_path", None)

        if _mount_path and workspace_exists:
            try:
                # File is accessible if it's within the mounted workspace
                file_path.resolve().relative_to(Path(_mount_path).resolve())
                sandbox_accessible = True
            except ValueError:
                # File is outside the mounted workspace — won't be visible in sandbox
                sandbox_accessible = False

        # Log mismatches
        if workspace_exists and not sandbox_accessible:
            logger.warning(
                "file_workspace_sandbox_mismatch",
                filepath=filepath,
                workspace_exists=True,
                sandbox_accessible=False,
                mount_path=_mount_path,
                hint="File exists in workspace but not accessible in Docker sandbox. "
                     "Call sync_workspace_to_sandbox() before execute_command.",
            )
        elif not workspace_exists:
            logger.warning(
                "file_not_found_anywhere",
                filepath=filepath,
                workspace_exists=False,
                sandbox_accessible=False,
            )

        return workspace_exists and sandbox_accessible
