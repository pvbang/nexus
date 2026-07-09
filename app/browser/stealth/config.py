"""Stealth browser configuration."""

import os
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class StealthEngine(str, Enum):
    PATCHRIGHT = "patchright"
    CAMOUFOX = "camoufox"
    AUTO = "auto"


class ProxyConfig(BaseModel):
    server: str | None = None
    username: str | None = None
    password: str | None = None
    bypass: str | None = None


class StealthConfig(BaseModel):
    engine: StealthEngine = Field(
        default=StealthEngine.AUTO,
        description="Browser engine. AUTO tries Patchright first, falls back to Camoufox.",
    )
    headless: bool = Field(
        default_factory=lambda: os.environ.get("BROWSER_USE_HEADLESS", "true").lower() == "true",
    )
    proxy: ProxyConfig | None = Field(
        default=None,
        description="Explicit proxy. If None, uses proxy pool.",
    )
    use_proxy_pool: bool = Field(
        default=True,
        description="Enable free proxy rotation when no explicit proxy set.",
    )
    fingerprint_randomize: bool = Field(
        default=True,
        description="Generate random realistic fingerprint per session.",
    )
    human_behavior: bool = Field(
        default=True,
        description="Simulate human-like delays and mouse movements.",
    )
    cloudflare_bypass: bool = Field(
        default=True,
        description="Enable Cloudflare Turnstile/challenge auto-bypass.",
    )
    byparr_url: str | None = Field(
        default_factory=lambda: os.environ.get("BYPARR_URL"),
        description="Byparr service URL for Cloudflare cookie extraction.",
    )
    max_retries: int = Field(default=3, description="Max retries on detection/block.")
    viewport_width: int = Field(default=1920)
    viewport_height: int = Field(default=1080)

    def get_env_overrides(self) -> dict[str, Any]:
        overrides = {}
        if env_engine := os.environ.get("STEALTH_ENGINE"):
            overrides["engine"] = StealthEngine(env_engine)
        if env_proxy := os.environ.get("STEALTH_PROXY_URL"):
            overrides["proxy"] = ProxyConfig(server=env_proxy)
        return overrides
