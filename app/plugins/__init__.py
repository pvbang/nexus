"""
MekongAI Nexus - Plugin Architecture (OpenClaw-inspired).

Manifest-driven plugin discovery, loading, and lifecycle management.
Plugins extend Nexus without modifying core code.

Inspired by: OpenClaw src/plugins/ — manifest-first control plane
"""

from app.plugins.manifest import PluginManifest, PluginCapability
from app.plugins.registry import PluginRegistry, get_plugin_registry
from app.plugins.hooks import HookRunner, HookEvent, get_hook_runner
from app.plugins.sdk import (
    PluginBase,
    ChannelPlugin,
    MemoryPlugin,
    ContextEnginePlugin,
    ToolProviderPlugin,
)

__all__ = [
    "PluginManifest",
    "PluginCapability",
    "PluginRegistry",
    "get_plugin_registry",
    "HookRunner",
    "HookEvent",
    "get_hook_runner",
    "PluginBase",
    "ChannelPlugin",
    "MemoryPlugin",
    "ContextEnginePlugin",
    "ToolProviderPlugin",
]
