"""
MekongAI Nexus - Plugin Registry & Loader (OpenClaw-inspired).

Filesystem-based plugin discovery, manifest validation, and safe loading.
"""

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

import structlog

from app.plugins.hooks import HookEvent, get_hook_runner
from app.plugins.manifest import PluginManifest, PluginCapability
from app.plugins.sdk import PluginBase

logger = structlog.get_logger(__name__)

PLUGIN_DIRS = [
    Path("plugins"),                     # workspace plugins
    Path("workspace_data/plugins"),      # user-installed plugins
]


class LoadedPlugin:
    """A validated and loaded plugin instance."""

    def __init__(self, manifest: PluginManifest, instance: PluginBase,
                 plugin_dir: Path):
        self.manifest = manifest
        self.instance = instance
        self.plugin_dir = plugin_dir
        self.active = False

    @property
    def id(self) -> str:
        return self.manifest.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.manifest.id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "capabilities": [c.value for c in self.manifest.capabilities],
            "active": self.active,
            "description": self.manifest.description,
        }


class PluginRegistry:
    """
    Discover, validate, and manage plugin lifecycle.

    Inspired by OpenClaw src/plugins/ — manifest-first discovery.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, LoadedPlugin] = {}
        self._scan_dirs: list[Path] = list(PLUGIN_DIRS)

    @property
    def plugins(self) -> dict[str, LoadedPlugin]:
        return dict(self._plugins)

    def add_scan_dir(self, path: Path) -> None:
        if path not in self._scan_dirs:
            self._scan_dirs.append(path)

    def discover(self) -> list[PluginManifest]:
        """Scan plugin dirs for plugin.json manifests."""
        manifests: list[PluginManifest] = []
        for scan_dir in self._scan_dirs:
            if not scan_dir.exists():
                continue
            for child in scan_dir.iterdir():
                if not child.is_dir():
                    continue
                manifest_file = child / "plugin.json"
                if not manifest_file.exists():
                    continue
                try:
                    data = json.loads(manifest_file.read_text(encoding="utf-8"))
                    manifest = PluginManifest(**data)
                    manifests.append(manifest)
                    logger.debug("plugin_discovered", id=manifest.id, path=str(child))
                except Exception:
                    logger.warning("plugin_manifest_invalid", path=str(manifest_file),
                                   exc_info=True)
        return manifests

    def _find_plugin_dir(self, plugin_id: str) -> Optional[Path]:
        """Find the directory for a plugin by id."""
        for scan_dir in self._scan_dirs:
            candidate = scan_dir / plugin_id
            if candidate.is_dir() and (candidate / "plugin.json").exists():
                return candidate
        return None

    def _load_module(self, plugin_dir: Path, manifest: PluginManifest) -> PluginBase:
        """Import the plugin entry point and instantiate the plugin class."""
        entry_path = plugin_dir / manifest.entry
        if not entry_path.exists():
            raise FileNotFoundError(f"Plugin entry not found: {entry_path}")

        module_name = f"nexus_plugin_{manifest.id}"
        spec = importlib.util.spec_from_file_location(module_name, str(entry_path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load plugin module: {entry_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        plugin_cls = getattr(module, manifest.plugin_class, None)
        if plugin_cls is None:
            raise AttributeError(
                f"Plugin class '{manifest.plugin_class}' not found in {entry_path}"
            )
        if not (isinstance(plugin_cls, type) and issubclass(plugin_cls, PluginBase)):
            raise TypeError(
                f"Plugin class must extend PluginBase, got {plugin_cls}"
            )
        return plugin_cls()

    async def load(self, plugin_id: str) -> LoadedPlugin:
        """Load a single plugin by id."""
        if plugin_id in self._plugins:
            return self._plugins[plugin_id]

        plugin_dir = self._find_plugin_dir(plugin_id)
        if plugin_dir is None:
            raise FileNotFoundError(f"Plugin not found: {plugin_id}")

        manifest_data = json.loads(
            (plugin_dir / "plugin.json").read_text(encoding="utf-8")
        )
        manifest = PluginManifest(**manifest_data)
        instance = self._load_module(plugin_dir, manifest)

        loaded = LoadedPlugin(manifest=manifest, instance=instance, plugin_dir=plugin_dir)
        self._plugins[plugin_id] = loaded

        # Register hooks declared in manifest
        hook_runner = get_hook_runner()
        for hook_decl in manifest.hooks:
            try:
                event = HookEvent(hook_decl.event)
            except ValueError:
                logger.warning("unknown_hook_event", plugin=plugin_id, event=hook_decl.event)
                continue
            handler_fn = getattr(instance, hook_decl.handler, None)
            if handler_fn and callable(handler_fn):
                hook_runner.register(plugin_id, event, handler_fn, hook_decl.priority)

        logger.info("plugin_loaded", id=plugin_id, name=manifest.name,
                     capabilities=[c.value for c in manifest.capabilities])
        return loaded

    async def activate(self, plugin_id: str) -> None:
        """Activate a loaded plugin."""
        loaded = self._plugins.get(plugin_id)
        if loaded is None:
            loaded = await self.load(plugin_id)
        if loaded.active:
            return
        await loaded.instance.activate()
        loaded.active = True
        logger.info("plugin_activated", id=plugin_id)

    async def deactivate(self, plugin_id: str) -> None:
        """Deactivate a plugin."""
        loaded = self._plugins.get(plugin_id)
        if loaded is None or not loaded.active:
            return
        await loaded.instance.deactivate()
        loaded.active = False
        get_hook_runner().unregister(plugin_id)
        logger.info("plugin_deactivated", id=plugin_id)

    async def load_all(self) -> list[str]:
        """Discover and load all plugins."""
        manifests = self.discover()
        loaded_ids: list[str] = []
        for manifest in manifests:
            try:
                await self.load(manifest.id)
                await self.activate(manifest.id)
                loaded_ids.append(manifest.id)
            except Exception:
                logger.warning("plugin_load_failed", id=manifest.id, exc_info=True)
        return loaded_ids

    async def shutdown_all(self) -> None:
        """Deactivate all plugins."""
        for plugin_id in list(self._plugins):
            try:
                await self.deactivate(plugin_id)
            except Exception:
                logger.warning("plugin_shutdown_error", id=plugin_id, exc_info=True)

    def get(self, plugin_id: str) -> Optional[LoadedPlugin]:
        return self._plugins.get(plugin_id)

    def get_by_capability(self, cap: PluginCapability) -> list[LoadedPlugin]:
        """Get all active plugins with a given capability."""
        return [
            p for p in self._plugins.values()
            if p.active and cap in p.manifest.capabilities
        ]

    def list_all(self) -> list[dict[str, Any]]:
        """Return info about all loaded plugins."""
        return [p.to_dict() for p in self._plugins.values()]


_registry: Optional[PluginRegistry] = None


def get_plugin_registry() -> PluginRegistry:
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry
