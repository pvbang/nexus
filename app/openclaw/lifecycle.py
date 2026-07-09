"""
MekongAI Nexus - OpenClaw Lifecycle Manager.

Manages OpenClaw Gateway as a subprocess of MekongAI Nexus.
Handles start, stop, restart, and crash recovery.

For production: prefer Docker Compose (both services as containers).
For development: Nexus can spawn OpenClaw as a child process.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional

import structlog

from app.openclaw.config import OpenClawConfig, get_openclaw_config

logger = structlog.get_logger(__name__)


class OpenClawLifecycle:
    """
    Manage OpenClaw Gateway subprocess lifecycle.

    Modes:
    1. SUBPROCESS: Nexus spawns and manages OpenClaw process
    2. EXTERNAL: OpenClaw runs independently (Docker Compose)

    Usage:
        lifecycle = OpenClawLifecycle()
        await lifecycle.start()    # Start OpenClaw
        await lifecycle.stop()     # Graceful stop
        lifecycle.is_running       # Check status
    """

    def __init__(self, config: Optional[OpenClawConfig] = None) -> None:
        self._config = config or get_openclaw_config()
        self._process: Optional[asyncio.subprocess.Process] = None
        self._monitor_task: Optional[asyncio.Task] = None
        self._restart_count = 0
        self._max_restarts = 5
        self._restart_cooldown = 10.0  # Seconds between restarts
        self._last_start_time = 0.0
        self._started = False

    @property
    def is_running(self) -> bool:
        """Check if OpenClaw process is running."""
        if self._process is None:
            return False
        return self._process.returncode is None

    @property
    def info(self) -> dict[str, Any]:
        return {
            "mode": "subprocess" if self._config.subprocess_enabled else "external",
            "running": self.is_running,
            "pid": self._process.pid if self._process else None,
            "restart_count": self._restart_count,
            "uptime_s": time.time() - self._last_start_time if self._started else 0,
        }

    async def start(self) -> bool:
        """Start OpenClaw Gateway.

        Returns True if started successfully (or already running).
        """
        if not self._config.subprocess_enabled:
            logger.info("openclaw_lifecycle_external_mode",
                        msg="OpenClaw managed externally (Docker/manual)")
            return True

        if self.is_running:
            logger.debug("openclaw_already_running")
            return True

        # Verify binary is available in PATH
        if not shutil.which(self._config.openclaw_binary):
            logger.warning(
                "openclaw_binary_unavailable",
                binary=self._config.openclaw_binary,
                msg="Skipping subprocess — binary not in PATH",
            )
            return False

        # Verify OpenClaw dist exists
        dist_path = Path(self._config.openclaw_dist)
        if not dist_path.exists():
            logger.error("openclaw_dist_not_found", path=str(dist_path))
            return False

        # Build command
        cmd = self._build_command()
        env = self._build_env()

        try:
            logger.info("openclaw_starting", cmd=" ".join(cmd[:4]))

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._last_start_time = time.time()
            self._started = True

            # Start monitor task
            self._monitor_task = asyncio.create_task(self._monitor_loop())

            # Wait briefly to check for immediate crash
            await asyncio.sleep(2.0)
            if not self.is_running:
                stderr = ""
                if self._process.stderr:
                    stderr = (await self._process.stderr.read(500)).decode("utf-8", errors="replace")
                logger.error("openclaw_failed_to_start", stderr=stderr[:200])
                return False

            logger.info("openclaw_started", pid=self._process.pid)
            return True

        except FileNotFoundError:
            logger.error("openclaw_binary_not_found",
                         binary=self._config.openclaw_binary)
            return False
        except Exception as exc:
            logger.error("openclaw_start_failed", error=str(exc))
            return False

    async def stop(self) -> None:
        """Gracefully stop OpenClaw Gateway."""
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        if self._process and self.is_running:
            logger.info("openclaw_stopping", pid=self._process.pid)

            # Send SIGTERM for graceful shutdown
            try:
                if sys.platform == "win32":
                    self._process.terminate()
                else:
                    self._process.send_signal(signal.SIGTERM)

                # Wait up to 10 seconds for graceful exit
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    # Force kill
                    logger.warning("openclaw_force_kill", pid=self._process.pid)
                    self._process.kill()
                    await self._process.wait()

            except ProcessLookupError:
                pass  # Already exited

            logger.info("openclaw_stopped")

        self._process = None
        self._started = False

    async def restart(self) -> bool:
        """Restart OpenClaw Gateway."""
        logger.info("openclaw_restarting")
        await self.stop()
        await asyncio.sleep(1.0)
        return await self.start()

    async def _monitor_loop(self) -> None:
        """Monitor OpenClaw process and auto-restart on crash."""
        while True:
            try:
                await asyncio.sleep(5.0)

                if self._process is None:
                    break

                if not self.is_running:
                    exit_code = self._process.returncode
                    logger.warning("openclaw_process_exited",
                                   exit_code=exit_code,
                                   restart_count=self._restart_count)

                    # Auto-restart if within limits
                    if self._restart_count < self._max_restarts:
                        elapsed = time.time() - self._last_start_time
                        if elapsed < self._restart_cooldown:
                            await asyncio.sleep(self._restart_cooldown - elapsed)

                        self._restart_count += 1
                        success = await self.start()
                        if not success:
                            logger.error("openclaw_restart_failed",
                                         attempts=self._restart_count)
                            break
                    else:
                        logger.error("openclaw_max_restarts_reached",
                                     max=self._max_restarts)
                        break

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("openclaw_monitor_error", error=str(exc))

    def _build_command(self) -> list[str]:
        """Build the OpenClaw start command."""
        cmd = [
            self._config.openclaw_binary,
            self._config.openclaw_dist,
            "gateway",
            "--bind", "lan",
            "--port", "18789",
        ]
        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for OpenClaw process."""
        env = dict(os.environ)
        env.update({
            "HOME": str(Path.home()),
            "TERM": "xterm-256color",
            "NODE_ENV": "production",
        })
        if self._config.gateway_token:
            env["OPENCLAW_GATEWAY_TOKEN"] = self._config.gateway_token
        return env


# ─────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────

_lifecycle: Optional[OpenClawLifecycle] = None


def get_openclaw_lifecycle() -> OpenClawLifecycle:
    """Get the singleton lifecycle manager."""
    global _lifecycle
    if _lifecycle is None:
        _lifecycle = OpenClawLifecycle()
    return _lifecycle
