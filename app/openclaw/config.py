"""
MekongAI Nexus - OpenClaw Connection Configuration.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class OpenClawConfig(BaseModel):
    """Configuration for connecting to OpenClaw Gateway."""

    gateway_url: str = Field(
        default="",
        description="OpenClaw Gateway HTTP URL (empty = disabled)",
    )
    gateway_token: str = Field(
        default="",
        description="Bearer token for authenticating with OpenClaw Gateway",
    )
    bridge_port: int = Field(
        default=18790,
        description="OpenClaw WebSocket bridge port",
    )
    timeout: float = Field(
        default=60.0,
        description="Default HTTP request timeout in seconds",
    )
    stream_timeout: float = Field(
        default=120.0,
        description="Timeout for streaming responses",
    )
    health_interval: float = Field(
        default=30.0,
        description="Interval for health checks in seconds",
    )
    max_retries: int = Field(
        default=3,
        description="Max retries for failed requests",
    )
    retry_delay: float = Field(
        default=1.0,
        description="Initial delay between retries (exponential backoff)",
    )
    internal_webhook_secret: str = Field(
        default="",
        description="Secret for verifying inbound webhooks from OpenClaw",
    )
    subprocess_enabled: bool = Field(
        default=False,
        description="If True, Nexus manages OpenClaw as a subprocess",
    )
    openclaw_binary: str = Field(
        default="node",
        description="Path to Node.js binary for subprocess mode",
    )
    openclaw_dist: str = Field(
        default="./src/open-source/openclaw/dist/index.js",
        description="Path to OpenClaw dist entry point",
    )
    workspace_dir: str = Field(
        default="./workspace_data",
        description="Shared workspace directory",
    )


_config: Optional[OpenClawConfig] = None


def get_openclaw_config() -> OpenClawConfig:
    """Get OpenClaw config from environment."""
    global _config
    if _config is None:
        import os
        _config = OpenClawConfig(
            gateway_url=os.environ.get("OPENCLAW_GATEWAY_URL", ""),
            gateway_token=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""),
            internal_webhook_secret=os.environ.get("OPENCLAW_INTERNAL_SECRET", ""),
            subprocess_enabled=os.environ.get("OPENCLAW_SUBPROCESS_ENABLED", "false").lower() == "true",
            openclaw_dist=os.environ.get("OPENCLAW_DIST_PATH", "./src/open-source/openclaw/dist/index.js"),
            workspace_dir=os.environ.get("OPENCLAW_WORKSPACE_DIR", "./workspace_data"),
        )
    return _config
