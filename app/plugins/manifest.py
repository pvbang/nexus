"""
MekongAI Nexus - Plugin Manifest (OpenClaw-inspired).

Pydantic models for plugin.json manifest validation.
"""

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class PluginCapability(str, Enum):
    """What a plugin can provide."""
    CHANNEL = "channel"
    MEMORY = "memory"
    CONTEXT_ENGINE = "context_engine"
    TOOL_PROVIDER = "tool_provider"
    HOOK = "hook"


class PluginHookDeclaration(BaseModel):
    """A hook this plugin wants to subscribe to."""
    event: str
    handler: str = "on_event"  # method name in plugin class
    priority: int = 100  # lower = runs first


class PluginToolDeclaration(BaseModel):
    """A tool this plugin provides."""
    name: str
    description: str = ""
    schema_: Optional[dict[str, Any]] = Field(None, alias="schema")


class PluginManifest(BaseModel):
    """
    Plugin manifest (plugin.json).

    Inspired by OpenClaw openclaw.plugin.json — manifest-first discovery.
    """
    id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$", min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    version: str = "0.1.0"
    description: str = ""
    author: str = ""

    capabilities: list[PluginCapability] = []
    hooks: list[PluginHookDeclaration] = []
    tools: list[PluginToolDeclaration] = []

    # Entry point
    entry: str = "main.py"
    plugin_class: str = "Plugin"  # class name to instantiate

    # Requirements
    min_host_version: Optional[str] = None
    dependencies: list[str] = []

    # Security
    requires_approval: bool = False
    dangerous: bool = False

    class Config:
        populate_by_name = True
