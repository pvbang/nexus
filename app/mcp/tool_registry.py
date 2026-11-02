"""
Nexus - Tool Registry (Hermes-agent inspired).

Central registry for all tools. Each tool file calls registry.register()
at module level to declare its schema, handler, toolset membership, and
availability check.

Import chain (circular-import safe):
    app/mcp/tool_registry.py  (no imports from tool files)
           ^
    app/mcp/tools/*.py  (import from tool_registry at module level)
           ^
    app/mcp/tool_executor.py  (imports tool_registry + all tool modules)

Inspired by: hermes-agent/tools/registry.py
"""

import asyncio
import inspect
import json
import logging
import threading
from typing import Any, Callable, Optional

import structlog

logger = structlog.get_logger(__name__)


class ToolEntry:
    """Metadata for a single registered tool."""

    __slots__ = (
        "name", "toolset", "category", "schema", "handler", "check_fn",
        "requires_env", "is_async", "description", "emoji",
        "max_result_size_chars",
    )

    def __init__(
        self,
        name: str,
        toolset: str,
        schema: dict,
        handler: Callable,
        check_fn: Optional[Callable] = None,
        requires_env: Optional[list[str]] = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "⚡",
        max_result_size_chars: Optional[int] = None,
        category: str = "",
    ):
        self.name = name
        self.toolset = toolset
        self.category = category or toolset  # Default: category = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.requires_env = requires_env or []
        self.is_async = is_async
        self.description = description or schema.get("description", "")
        self.emoji = emoji
        self.max_result_size_chars = max_result_size_chars


class ToolRegistry:
    """
    Singleton registry that collects tool schemas + handlers from tool files.

    Thread-safe: MCP dynamic refresh can mutate the registry while other
    threads are reading tool metadata.

    Inspired by hermes-agent ToolRegistry pattern.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._toolset_checks: dict[str, Callable] = {}
        self._toolset_aliases: dict[str, str] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Snapshots (thread-safe reads)
    # ------------------------------------------------------------------

    def _snapshot_entries(self) -> list[ToolEntry]:
        """Return a stable snapshot of registered tool entries."""
        with self._lock:
            return list(self._tools.values())

    def _snapshot_toolset_checks(self) -> dict[str, Callable]:
        """Return a stable snapshot of toolset availability checks."""
        with self._lock:
            return dict(self._toolset_checks)

    def _evaluate_toolset_check(self, toolset: str, check: Optional[Callable]) -> bool:
        """Run a toolset check, treating missing or failing checks as available."""
        if not check:
            return True
        try:
            return bool(check())
        except Exception:
            logger.debug("toolset_check_failed", toolset=toolset)
            return False

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_entry(self, name: str) -> Optional[ToolEntry]:
        """Return a registered tool entry by name, or None."""
        with self._lock:
            return self._tools.get(name)

    def get_all_tool_names(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(entry.name for entry in self._snapshot_entries())

    def get_registered_toolset_names(self) -> list[str]:
        """Return sorted unique toolset names present in the registry."""
        return sorted({entry.toolset for entry in self._snapshot_entries()})

    def get_tool_names_for_toolset(self, toolset: str) -> list[str]:
        """Return sorted tool names registered under a given toolset."""
        return sorted(
            entry.name for entry in self._snapshot_entries()
            if entry.toolset == toolset
        )

    def get_toolset_for_tool(self, name: str) -> Optional[str]:
        """Return the toolset a tool belongs to, or None."""
        entry = self.get_entry(name)
        return entry.toolset if entry else None

    def get_emoji(self, name: str, default: str = "⚡") -> str:
        """Return the emoji for a tool, or default."""
        entry = self.get_entry(name)
        return (entry.emoji if entry and entry.emoji else default)

    def get_schema(self, name: str) -> Optional[dict]:
        """Return a tool's raw schema dict."""
        entry = self.get_entry(name)
        return entry.schema if entry else None

    def get_max_result_size(self, name: str, default: int = 10_000) -> int:
        """Return per-tool max result size, or default."""
        entry = self.get_entry(name)
        if entry and entry.max_result_size_chars is not None:
            return entry.max_result_size_chars
        return default

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        toolset: str,
        schema: Optional[dict] = None,
        handler: Optional[Callable] = None,
        check_fn: Optional[Callable] = None,
        requires_env: Optional[list[str]] = None,
        is_async: bool = False,
        description: str = "",
        emoji: str = "⚡",
        max_result_size_chars: Optional[int] = None,
        category: str = "",
        *,
        parameters: Optional[dict] = None,
    ) -> None:
        """Register a tool. Called at module-import time by each tool file.

        Args:
            schema: Tool parameter schema (OpenAI function format).
            parameters: Alias for schema — if provided and schema is None,
                        auto-wraps into proper schema format.
        """
        # COROUTINE-FIX: Auto-detect async handlers to prevent unawaited coroutines.
        # Previously, async handlers registered without is_async=True would return
        # coroutine objects instead of actual results (shown as "<coroutine object...>").
        if handler is not None and not is_async:
            if inspect.iscoroutinefunction(handler):
                is_async = True

        # Support 'parameters' as alias for 'schema'
        if schema is None and parameters is not None:
            schema = {
                "type": "object",
                "properties": parameters.get("properties", {}),
                "required": parameters.get("required", []),
            }
        if schema is None:
            schema = {"type": "object", "properties": {}}
        with self._lock:
            existing = self._tools.get(name)
            if existing and existing.toolset != toolset:
                # Reject shadowing — prevent tools from overwriting each other
                logger.error(
                    "tool_registration_rejected",
                    name=name,
                    new_toolset=toolset,
                    existing_toolset=existing.toolset,
                )
                return

            self._tools[name] = ToolEntry(
                name=name,
                toolset=toolset,
                schema=schema,
                handler=handler,
                check_fn=check_fn,
                requires_env=requires_env,
                is_async=is_async,
                description=description or schema.get("description", ""),
                emoji=emoji,
                max_result_size_chars=max_result_size_chars,
                category=category,
            )
            if check_fn and toolset not in self._toolset_checks:
                self._toolset_checks[toolset] = check_fn

        logger.debug("tool_registered", name=name, toolset=toolset, category=category or toolset)

    def deregister(self, name: str) -> None:
        """Remove a tool from the registry."""
        with self._lock:
            entry = self._tools.pop(name, None)
            if entry is None:
                return
            # Drop the toolset check if this was the last tool
            toolset_still_exists = any(
                e.toolset == entry.toolset for e in self._tools.values()
            )
            if not toolset_still_exists:
                self._toolset_checks.pop(entry.toolset, None)
        logger.debug("tool_deregistered", name=name)

    def register_toolset_alias(self, alias: str, toolset: str) -> None:
        """Register an explicit alias for a canonical toolset name."""
        with self._lock:
            self._toolset_aliases[alias] = toolset

    # ------------------------------------------------------------------
    # Schema retrieval (for LLM function calling)
    # ------------------------------------------------------------------

    def get_definitions(
        self,
        tool_names: Optional[set[str]] = None,
        toolsets: Optional[set[str]] = None,
    ) -> list[dict]:
        """Return OpenAI-format tool schemas for the requested tools.

        Only tools whose check_fn() returns True are included.
        If tool_names is None and toolsets is None, return all available.
        """
        result = []
        check_results: dict[Callable, bool] = {}
        entries = self._snapshot_entries()

        for entry in sorted(entries, key=lambda e: e.name):
            # Filter by name or toolset
            if tool_names and entry.name not in tool_names:
                continue
            if toolsets and entry.toolset not in toolsets:
                continue

            # Check availability
            if entry.check_fn:
                if entry.check_fn not in check_results:
                    try:
                        check_results[entry.check_fn] = bool(entry.check_fn())
                    except Exception:
                        check_results[entry.check_fn] = False
                if not check_results[entry.check_fn]:
                    continue

            schema_with_name = {**entry.schema, "name": entry.name}
            result.append({"type": "function", "function": schema_with_name})
        return result

    def get_tool_index(
        self,
        tool_names: Optional[set[str]] = None,
        toolsets: Optional[set[str]] = None,
    ) -> list[dict]:
        """Return a compact {name, description} index for deferred tool search.

        Cheap to keep in the prompt when an agent has access to many tools; the
        model promotes full schemas on demand via ``search_tools``.
        """
        from app.mcp.tool_search import build_tool_index
        return build_tool_index(self.get_definitions(tool_names=tool_names, toolsets=toolsets))

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, name: str, args: dict, **kwargs) -> str:
        """Execute a tool handler by name. Returns JSON string."""
        entry = self.get_entry(name)
        if not entry:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            if entry.is_async:
                result = await entry.handler(args, **kwargs)
            else:
                # Run synchronous handlers in a worker thread. Many sync tools are
                # CPU-bound or block on subprocesses (OCR, video/ffmpeg, document
                # conversion, network calls). Calling them inline would stall the
                # single event loop for the whole run — starving the heartbeat loop
                # and SSE emits, which previously caused the watchdog to mark a live
                # task "crashed". to_thread keeps the loop responsive during them.
                result = await asyncio.to_thread(
                    lambda: entry.handler(args, **kwargs)
                )

            # COROUTINE-FIX: Safety net — await if handler returned a coroutine
            # despite is_async=False (e.g., dynamically created async wrapper).
            if asyncio.iscoroutine(result):
                result = await result

            # Ensure result is JSON string
            if isinstance(result, dict):
                return json.dumps(result, ensure_ascii=False)
            return str(result)
        except Exception as e:
            logger.exception("tool_dispatch_error", tool=name, error=str(e))
            return json.dumps({"error": f"Tool execution failed: {type(e).__name__}: {e}"})

    # ------------------------------------------------------------------
    # Toolset availability
    # ------------------------------------------------------------------

    def is_toolset_available(self, toolset: str) -> bool:
        """Check if a toolset's requirements are met."""
        with self._lock:
            check = self._toolset_checks.get(toolset)
        return self._evaluate_toolset_check(toolset, check)

    def check_toolset_requirements(self) -> dict[str, bool]:
        """Return {toolset: available_bool} for every toolset."""
        entries = self._snapshot_entries()
        checks = self._snapshot_toolset_checks()
        toolsets = sorted({entry.toolset for entry in entries})
        return {
            toolset: self._evaluate_toolset_check(toolset, checks.get(toolset))
            for toolset in toolsets
        }

    def get_available_toolsets(self) -> dict[str, dict]:
        """Return toolset metadata for UI display."""
        toolsets: dict[str, dict] = {}
        entries = self._snapshot_entries()
        checks = self._snapshot_toolset_checks()

        for entry in entries:
            ts = entry.toolset
            if ts not in toolsets:
                toolsets[ts] = {
                    "available": self._evaluate_toolset_check(ts, checks.get(ts)),
                    "tools": [],
                    "description": "",
                    "requirements": [],
                }
            toolsets[ts]["tools"].append(entry.name)
            for env in entry.requires_env:
                if env not in toolsets[ts]["requirements"]:
                    toolsets[ts]["requirements"].append(env)
        return toolsets

    def get_tool_to_toolset_map(self) -> dict[str, str]:
        """Return {tool_name: toolset_name} for every registered tool."""
        return {entry.name: entry.toolset for entry in self._snapshot_entries()}

    # ------------------------------------------------------------------
    # Category queries (PromptBuilder / ToolPolicy support)
    # ------------------------------------------------------------------

    def get_categories(self) -> list[str]:
        """Return sorted unique category names."""
        return sorted({entry.category for entry in self._snapshot_entries()})

    def get_tool_names_for_category(self, category: str) -> list[str]:
        """Return sorted tool names in a given category."""
        return sorted(
            entry.name for entry in self._snapshot_entries()
            if entry.category == category
        )

    def get_tool_catalog(self) -> dict[str, list[dict[str, str]]]:
        """Return categorized tool catalog for prompt generation.

        Returns {category: [{name, description, emoji}, ...]} sorted
        deterministically for prompt cache stability (OpenClaw pattern).
        """
        catalog: dict[str, list[dict[str, str]]] = {}
        entries = self._snapshot_entries()
        checks = self._snapshot_toolset_checks()

        for entry in sorted(entries, key=lambda e: (e.category, e.name)):
            # Skip unavailable tools
            if entry.check_fn:
                available = self._evaluate_toolset_check(
                    entry.toolset, checks.get(entry.toolset, entry.check_fn),
                )
                if not available:
                    continue

            cat = entry.category
            if cat not in catalog:
                catalog[cat] = []
            catalog[cat].append({
                "name": entry.name,
                "description": entry.description,
                "emoji": entry.emoji,
            })
        return catalog

    def get_tool_to_category_map(self) -> dict[str, str]:
        """Return {tool_name: category} for every registered tool."""
        return {entry.name: entry.category for entry in self._snapshot_entries()}


# Module-level singleton
registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    """Get the module-level ToolRegistry singleton."""
    return registry


# ---------------------------------------------------------------------------
# Helpers for tool response serialization
# ---------------------------------------------------------------------------

def tool_error(message: str, **extra) -> str:
    """Return a JSON error string for tool handlers.

    >>> tool_error("file not found")
    '{"error": "file not found"}'
    """
    result = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data: Any = None, **kwargs) -> str:
    """Return a JSON result string for tool handlers.

    >>> tool_result(success=True, count=42)
    '{"success": true, "count": 42}'

    When both data and kwargs are provided, kwargs are included
    in the result dict (e.g., success, output) alongside data.
    """
    if data is not None:
        # When data is a dict, merge kwargs (success, output, etc.) into it
        if isinstance(data, dict):
            merged = {**kwargs, "data": data}
            return json.dumps(merged, ensure_ascii=False)
        # For non-dict data, wrap in a result dict with kwargs
        result = {**kwargs, "data": data}
        return json.dumps(result, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
