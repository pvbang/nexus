"""
MekongAI Nexus - Hermes Agent Process Manager.

Manages the Hermes Agent subprocess lifecycle:
- Start/stop/restart
- Health monitoring with auto-restart
- Graceful shutdown on app exit
- Exponential backoff on repeated failures
"""

import asyncio
import enum
import os
import signal
import time
from typing import Optional

import structlog

from app.hermes.config import HermesSettings, get_hermes_settings

logger = structlog.get_logger(__name__)


class HermesStatus(str, enum.Enum):
    """Process status."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"
    DISABLED = "disabled"


class HermesProcessManager:
    """
    Manages the Hermes Agent as a subprocess.

    Hermes runs as an MCP server (mcp_serve.py) that MekongAI connects to
    via stdio or HTTP transport.

    Lifecycle:
        start() → health_loop() → stop()
              ↑                     │
              └── auto_restart ←────┘ (on crash)
    """

    def __init__(self, settings: Optional[HermesSettings] = None) -> None:
        self._settings = settings or get_hermes_settings()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._status = HermesStatus.DISABLED if not self._settings.enabled else HermesStatus.STOPPED
        self._health_task: Optional[asyncio.Task] = None
        self._restart_count: int = 0
        self._last_start_time: float = 0.0
        self._start_lock = asyncio.Lock()
        self._stdout_lines: list[str] = []  # Recent stdout (ring buffer)
        self._stderr_lines: list[str] = []  # Recent stderr (ring buffer)
        self._max_log_lines: int = 200

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def status(self) -> HermesStatus:
        return self._status

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    @property
    def uptime_seconds(self) -> float:
        if self._status == HermesStatus.RUNNING and self._last_start_time > 0:
            return time.monotonic() - self._last_start_time
        return 0.0

    @property
    def recent_stdout(self) -> list[str]:
        return list(self._stdout_lines)

    @property
    def recent_stderr(self) -> list[str]:
        return list(self._stderr_lines)

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Start the Hermes Agent subprocess.

        Returns True if started successfully.
        """
        if not self._settings.enabled:
            logger.info("hermes_disabled")
            self._status = HermesStatus.DISABLED
            return False

        async with self._start_lock:
            if self._status == HermesStatus.RUNNING:
                logger.info("hermes_already_running", pid=self.pid)
                return True

            # Validate config
            errors = self._settings.validate()
            if errors:
                for err in errors:
                    logger.error("hermes_config_error", error=err)
                self._status = HermesStatus.FAILED
                return False

            self._status = HermesStatus.STARTING
            logger.info(
                "hermes_starting",
                transport=self._settings.transport,
                path=self._settings.hermes_abs_path,
            )

            try:
                cmd = self._build_command()
                env = self._build_env()

                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if self._settings.transport == "stdio" else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                    cwd=self._settings.hermes_abs_path,
                )

                self._status = HermesStatus.RUNNING
                self._last_start_time = time.monotonic()
                self._restart_count = 0

                logger.info(
                    "hermes_started",
                    pid=self._process.pid,
                    transport=self._settings.transport,
                )

                # Start background log readers
                asyncio.create_task(
                    self._read_stream(self._process.stdout, self._stdout_lines, "stdout")
                )
                asyncio.create_task(
                    self._read_stream(self._process.stderr, self._stderr_lines, "stderr")
                )

                # Start health monitor
                if self._settings.auto_restart:
                    self._health_task = asyncio.create_task(self._health_loop())

                return True

            except FileNotFoundError as exc:
                logger.error("hermes_start_failed_not_found", error=str(exc))
                self._status = HermesStatus.FAILED
                return False
            except Exception as exc:
                logger.error("hermes_start_failed", error=str(exc), exc_info=True)
                self._status = HermesStatus.FAILED
                return False

    def _build_command(self) -> list[str]:
        """Build the subprocess command."""
        cmd = [self._settings.hermes_python, self._settings.mcp_serve_path]

        if self._settings.transport == "http":
            cmd.extend(["--host", self._settings.http_host])
            cmd.extend(["--port", str(self._settings.http_port)])

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for Hermes subprocess."""
        env = dict(os.environ)

        # Always set HERMES_PATH so mcp_wrapper can import Hermes modules
        env["HERMES_PATH"] = self._settings.hermes_abs_path

        # Override Hermes-specific env vars
        if self._settings.hermes_home:
            env["HERMES_HOME"] = self._settings.hermes_home

        if self._settings.default_model:
            env["HERMES_MODEL"] = self._settings.default_model

        if self._settings.default_provider:
            env["HERMES_PROVIDER"] = self._settings.default_provider

        if self._settings.terminal_backend:
            env["HERMES_TERMINAL_BACKEND"] = self._settings.terminal_backend

        # Load Hermes .env file if specified
        if self._settings.hermes_env_file and os.path.isfile(self._settings.hermes_env_file):
            env.update(self._parse_env_file(self._settings.hermes_env_file))

        return env

    @staticmethod
    def _parse_env_file(path: str) -> dict[str, str]:
        """Parse a simple .env file into a dict."""
        result: dict[str, str] = {}
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, _, value = line.partition("=")
                        key = key.strip()
                        value = value.strip().strip("'\"")
                        if key:
                            result[key] = value
        except Exception as exc:
            logger.warning("hermes_env_file_parse_error", path=path, error=str(exc))
        return result

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """Stop the Hermes Agent subprocess gracefully."""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

        if not self._process:
            self._status = HermesStatus.STOPPED
            return

        self._status = HermesStatus.STOPPING
        logger.info("hermes_stopping", pid=self._process.pid)

        try:
            # Try graceful shutdown via stdin EOF (for stdio transport)
            if self._process.stdin:
                try:
                    self._process.stdin.close()
                except Exception:
                    pass

            # Send SIGTERM
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass

            # Wait for graceful exit
            try:
                await asyncio.wait_for(
                    self._process.wait(),
                    timeout=self._settings.shutdown_timeout,
                )
                logger.info("hermes_stopped_gracefully", pid=self._process.pid)
            except asyncio.TimeoutError:
                # Force kill
                logger.warning("hermes_force_killing", pid=self._process.pid)
                try:
                    self._process.kill()
                    await asyncio.wait_for(self._process.wait(), timeout=50.0)
                except (ProcessLookupError, asyncio.TimeoutError):
                    pass

        except Exception as exc:
            logger.error("hermes_stop_error", error=str(exc))

        self._process = None
        self._status = HermesStatus.STOPPED

    # ------------------------------------------------------------------
    # Restart
    # ------------------------------------------------------------------

    async def restart(self) -> bool:
        """Restart the Hermes Agent."""
        logger.info("hermes_restarting")
        await self.stop()
        await asyncio.sleep(1.0)  # Brief pause
        return await self.start()

    # ------------------------------------------------------------------
    # Health Monitoring
    # ------------------------------------------------------------------

    async def _health_loop(self) -> None:
        """Background task: monitor process health, auto-restart on crash."""
        try:
            while True:
                await asyncio.sleep(self._settings.health_check_interval)

                if self._status != HermesStatus.RUNNING:
                    continue

                if self._process is None:
                    continue

                # Check if process is still alive
                return_code = self._process.returncode
                if return_code is not None:
                    logger.warning(
                        "hermes_process_exited",
                        return_code=return_code,
                        restart_count=self._restart_count,
                    )

                    if not self._settings.auto_restart:
                        self._status = HermesStatus.FAILED
                        break

                    if self._restart_count >= self._settings.max_restart_attempts:
                        logger.error(
                            "hermes_max_restarts_reached",
                            max_attempts=self._settings.max_restart_attempts,
                        )
                        self._status = HermesStatus.FAILED
                        break

                    # Exponential backoff
                    self._restart_count += 1
                    backoff = self._settings.restart_backoff_base ** self._restart_count
                    backoff = min(backoff, 60.0)  # Cap at 60s
                    logger.info(
                        "hermes_auto_restart",
                        attempt=self._restart_count,
                        backoff_seconds=backoff,
                    )
                    await asyncio.sleep(backoff)

                    self._process = None
                    self._status = HermesStatus.STOPPED
                    success = await self.start()
                    if not success:
                        self._status = HermesStatus.FAILED
                        break

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("hermes_health_loop_error", error=str(exc), exc_info=True)

    async def health_check(self) -> dict:
        """Return current health status."""
        return {
            "status": self._status.value,
            "pid": self.pid,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "restart_count": self._restart_count,
            "transport": self._settings.transport,
            "terminal_backend": self._settings.terminal_backend,
            "enabled": self._settings.enabled,
            "hermes_path": self._settings.hermes_abs_path,
        }

    # ------------------------------------------------------------------
    # Log Streaming
    # ------------------------------------------------------------------

    async def _read_stream(
        self,
        stream: Optional[asyncio.StreamReader],
        buffer: list[str],
        stream_name: str,
    ) -> None:
        """Read subprocess stream into ring buffer."""
        if not stream:
            return

        try:
            while True:
                line = await stream.readline()
                if not line:
                    break

                decoded = line.decode("utf-8", errors="replace").rstrip()
                buffer.append(decoded)

                # Ring buffer: keep last N lines
                if len(buffer) > self._max_log_lines:
                    del buffer[: len(buffer) - self._max_log_lines]

                # Log important messages
                if stream_name == "stderr" and decoded:
                    logger.debug("hermes_stderr", line=decoded[:500])

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("hermes_stream_read_error", stream=stream_name, error=str(exc))

    # ------------------------------------------------------------------
    # Direct stdin/stdout (for stdio transport)
    # ------------------------------------------------------------------

    @property
    def stdin(self) -> Optional[asyncio.StreamWriter]:
        """Access subprocess stdin (for stdio MCP transport)."""
        if self._process and self._status == HermesStatus.RUNNING:
            return self._process.stdin
        return None

    @property
    def stdout(self) -> Optional[asyncio.StreamReader]:
        """Access subprocess stdout (for stdio MCP transport)."""
        if self._process and self._status == HermesStatus.RUNNING:
            return self._process.stdout
        return None


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_manager: Optional[HermesProcessManager] = None


def get_hermes_manager() -> HermesProcessManager:
    """Get or create the global Hermes process manager."""
    global _manager
    if _manager is None:
        _manager = HermesProcessManager()
    return _manager
