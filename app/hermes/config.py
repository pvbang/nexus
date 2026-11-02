"""
Nexus - Hermes Agent Configuration.

All Hermes-specific settings, resolved from environment variables.
"""

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Default path relative to project root
_DEFAULT_HERMES_PATH = os.path.join("src", "open-source", "hermes-agent")


@dataclass(frozen=True)
class HermesSettings:
    """Immutable configuration for Hermes Agent integration."""

    # --- Paths ---
    hermes_path: str = _DEFAULT_HERMES_PATH
    hermes_python: str = "python"  # Python executable for Hermes venv
    hermes_entry_point: str = ""  # Auto-detected: uses app/hermes/mcp_wrapper.py

    # --- Connection ---
    transport: str = "stdio"  # stdio | http
    http_host: str = "127.0.0.1"
    http_port: int = 9100
    connect_timeout: float = 30.0
    request_timeout: float = 120.0

    # --- Process Management ---
    auto_start: bool = True  # Start Hermes on Nexus startup
    auto_restart: bool = True  # Restart on crash
    max_restart_attempts: int = 3
    restart_backoff_base: float = 2.0  # seconds, exponential backoff
    health_check_interval: float = 30.0  # seconds
    shutdown_timeout: float = 10.0  # seconds to wait for graceful shutdown

    # --- Hermes Agent Config ---
    default_model: str = ""  # If empty, use Hermes default
    default_provider: str = ""  # If empty, use Hermes default
    terminal_backend: str = "local"  # local | docker | ssh | modal | daytona | singularity
    hermes_home: str = ""  # Override HERMES_HOME env var
    hermes_env_file: str = ""  # Path to Hermes .env file

    # --- Batch Runner ---
    batch_max_parallel: int = 4
    batch_checkpoint_dir: str = "workspace_data/hermes_batch"

    # --- Feature Flags ---
    enabled: bool = True
    expose_terminal_tools: bool = True
    expose_batch_runner: bool = True
    expose_code_edit: bool = True
    expose_session_search: bool = True

    @property
    def hermes_abs_path(self) -> str:
        """Absolute path to Hermes Agent directory."""
        if os.path.isabs(self.hermes_path):
            return self.hermes_path
        return os.path.abspath(self.hermes_path)

    @property
    def mcp_serve_path(self) -> str:
        """Absolute path to MCP wrapper module."""
        if self.hermes_entry_point:
            return os.path.join(self.hermes_abs_path, self.hermes_entry_point)
        # Default: use our custom MCP wrapper (app/hermes/mcp_wrapper.py)
        return os.path.join(os.path.dirname(__file__), "mcp_wrapper.py")

    @property
    def http_url(self) -> str:
        """HTTP URL for Hermes MCP server."""
        return f"http://{self.http_host}:{self.http_port}"

    def validate(self) -> list[str]:
        """Validate configuration. Returns list of error messages (empty = OK)."""
        errors: list[str] = []

        if not os.path.isdir(self.hermes_abs_path):
            errors.append(f"Hermes directory not found: {self.hermes_abs_path}")

        mcp_path = self.mcp_serve_path
        if not os.path.isfile(mcp_path):
            errors.append(f"MCP wrapper not found: {mcp_path}")

        if self.transport not in ("stdio", "http"):
            errors.append(f"Invalid transport: {self.transport} (must be stdio|http)")

        valid_backends = {"local", "docker", "ssh", "modal", "daytona", "singularity"}
        if self.terminal_backend not in valid_backends:
            errors.append(
                f"Invalid terminal_backend: {self.terminal_backend} "
                f"(must be one of {valid_backends})"
            )

        return errors


def _load_from_env() -> HermesSettings:
    """Load HermesSettings from environment variables with HERMES_ prefix."""
    env = os.environ

    def _get(key: str, default: str = "") -> str:
        return env.get(f"HERMES_{key}", default)

    def _get_bool(key: str, default: bool = False) -> bool:
        val = _get(key, "").lower()
        if val in ("1", "true", "yes", "on"):
            return True
        if val in ("0", "false", "no", "off"):
            return False
        return default

    def _get_int(key: str, default: int = 0) -> int:
        val = _get(key, "")
        if val.isdigit():
            return int(val)
        return default

    def _get_float(key: str, default: float = 0.0) -> float:
        val = _get(key, "")
        try:
            return float(val) if val else default
        except ValueError:
            return default

    return HermesSettings(
        hermes_path=_get("PATH", _DEFAULT_HERMES_PATH),
        hermes_python=_get("PYTHON", "python"),
        hermes_entry_point=_get("ENTRY_POINT", "mcp_serve.py"),
        transport=_get("TRANSPORT", "stdio"),
        http_host=_get("HTTP_HOST", "127.0.0.1"),
        http_port=_get_int("HTTP_PORT", 9100),
        connect_timeout=_get_float("CONNECT_TIMEOUT", 300.0),
        request_timeout=_get_float("REQUEST_TIMEOUT", 1200.0),
        auto_start=_get_bool("AUTO_START", True),
        auto_restart=_get_bool("AUTO_RESTART", True),
        max_restart_attempts=_get_int("MAX_RESTART_ATTEMPTS", 3),
        restart_backoff_base=_get_float("RESTART_BACKOFF_BASE", 2.0),
        health_check_interval=_get_float("HEALTH_CHECK_INTERVAL", 30.0),
        shutdown_timeout=_get_float("SHUTDOWN_TIMEOUT", 10.0),
        default_model=_get("DEFAULT_MODEL", ""),
        default_provider=_get("DEFAULT_PROVIDER", ""),
        terminal_backend=_get("TERMINAL_BACKEND", "local"),
        hermes_home=_get("HOME", ""),
        hermes_env_file=_get("ENV_FILE", ""),
        batch_max_parallel=_get_int("BATCH_MAX_PARALLEL", 4),
        batch_checkpoint_dir=_get("BATCH_CHECKPOINT_DIR", "workspace_data/hermes_batch"),
        enabled=_get_bool("ENABLED", True),
        expose_terminal_tools=_get_bool("EXPOSE_TERMINAL_TOOLS", True),
        expose_batch_runner=_get_bool("EXPOSE_BATCH_RUNNER", True),
        expose_code_edit=_get_bool("EXPOSE_CODE_EDIT", True),
        expose_session_search=_get_bool("EXPOSE_SESSION_SEARCH", True),
    )


@lru_cache()
def get_hermes_settings() -> HermesSettings:
    """Cached singleton for Hermes settings."""
    settings = _load_from_env()
    errors = settings.validate()
    if errors:
        for err in errors:
            logger.warning("hermes_config_validation", error=err)
    else:
        logger.info(
            "hermes_config_loaded",
            path=settings.hermes_abs_path,
            transport=settings.transport,
            terminal_backend=settings.terminal_backend,
            enabled=settings.enabled,
        )
    return settings
