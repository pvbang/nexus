"""
Nexus - Pluggable Context Engine (OpenClaw-inspired).

Abstract interface for context management with registry pattern.
Allows swapping context strategies without touching core code.

Inspired by: OpenClaw src/context-engine/types.ts + registry.ts
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types (mirrors OpenClaw ContextEngine types)
# ---------------------------------------------------------------------------

@dataclass
class ContextEngineInfo:
    id: str
    name: str
    version: str = "0.1.0"
    owns_compaction: bool = False


@dataclass
class AssembleResult:
    """Result of assembling context for a model call."""
    messages: list[dict[str, Any]] = field(default_factory=list)
    estimated_tokens: int = 0
    system_prompt_addition: str = ""


@dataclass
class CompactResult:
    ok: bool = True
    compacted: bool = False
    reason: str = ""
    summary: str = ""
    tokens_before: int = 0
    tokens_after: int = 0


@dataclass
class IngestResult:
    ingested: bool = True


@dataclass
class BootstrapResult:
    bootstrapped: bool = False
    imported_messages: int = 0
    reason: str = ""


@dataclass
class MaintenanceResult:
    changed: bool = False
    bytes_freed: int = 0
    rewritten_entries: int = 0


# ---------------------------------------------------------------------------
# Abstract ContextEngine interface
# ---------------------------------------------------------------------------

class ContextEngine(ABC):
    """
    Pluggable contract for context management.

    Mirrors OpenClaw ContextEngine interface:
    - Required: info, ingest, assemble, compact
    - Optional: bootstrap, maintain, after_turn, prepare_subagent_spawn
    """

    @property
    @abstractmethod
    def info(self) -> ContextEngineInfo:
        """Engine identifier and metadata."""

    @abstractmethod
    async def ingest(self, session_id: str, message: dict[str, Any]) -> IngestResult:
        """Ingest a single message into the engine's store."""

    @abstractmethod
    async def assemble(self, session_id: str, messages: list[dict[str, Any]],
                       token_budget: int = 128_000,
                       available_tools: Optional[set[str]] = None,
                       prompt: str = "") -> AssembleResult:
        """Assemble model context under a token budget."""

    @abstractmethod
    async def compact(self, session_id: str, messages: list[dict[str, Any]],
                      token_budget: int = 128_000,
                      force: bool = False,
                      current_token_count: int = 0) -> CompactResult:
        """Compact context to reduce token usage."""

    # Optional lifecycle hooks (defaults are no-ops)

    async def bootstrap(self, session_id: str, session_file: str = "") -> BootstrapResult:
        return BootstrapResult()

    async def maintain(self, session_id: str, session_file: str = "") -> MaintenanceResult:
        return MaintenanceResult()

    async def after_turn(self, session_id: str, messages: list[dict[str, Any]],
                         pre_prompt_count: int = 0) -> None:
        pass

    async def prepare_subagent_spawn(self, parent_key: str,
                                     child_key: str) -> None:
        pass

    async def on_messages_compacted(
        self, session_id: str, summary: str, removed_count: int
    ) -> None:
        """Called after messages have been compacted into a summary.

        Subclasses can override to persist compaction metadata or
        update external indexes.
        """
        logger.debug(
            "context_engine_messages_compacted",
            session_id=session_id,
            summary_len=len(summary),
            removed_count=removed_count,
        )

    async def on_child_agent_spawned(
        self, parent_session_id: str, child_session_id: str, child_role: str
    ) -> None:
        """Called when a child agent is spawned from a parent session.

        Subclasses can override to share context between parent and child.
        """
        logger.debug(
            "context_engine_child_spawned",
            parent=parent_session_id,
            child=child_session_id,
            role=child_role,
        )

    async def on_session_reset(self) -> None:
        """Called when a session is reset / cleared.

        Subclasses should clear any cached context for the session.
        """
        logger.debug("context_engine_session_reset")

    async def dispose(self) -> None:
        """Cleanup resources held by this engine instance.

        Called during application shutdown. Subclasses should release
        connections, flush buffers, and cancel pending tasks.
        """
        logger.debug("context_engine_disposed")


# ---------------------------------------------------------------------------
# Context Engine Registry (singleton, process-global)
# ---------------------------------------------------------------------------

ContextEngineFactory = Callable[[], ContextEngine | Awaitable[ContextEngine]]

_DEFAULT_ENGINE_ID = "legacy"


class ContextEngineRegistry:
    """
    Registry for context engine implementations.

    Mirrors OpenClaw registerContextEngine/resolveContextEngine pattern.
    """

    def __init__(self) -> None:
        self._engines: dict[str, ContextEngineFactory] = {}

    def register(self, engine_id: str, factory: ContextEngineFactory) -> bool:
        """Register a context engine factory. Returns False if id already taken."""
        if engine_id in self._engines:
            logger.warning("context_engine_already_registered", id=engine_id)
            return False
        self._engines[engine_id] = factory
        logger.info("context_engine_registered", id=engine_id)
        return True

    def deregister(self, engine_id: str) -> bool:
        if engine_id in self._engines:
            del self._engines[engine_id]
            return True
        return False

    def list_ids(self) -> list[str]:
        return list(self._engines.keys())

    async def resolve(self, engine_id: Optional[str] = None) -> ContextEngine:
        """Resolve a context engine by id. Falls back to default."""
        eid = engine_id or _DEFAULT_ENGINE_ID
        factory = self._engines.get(eid)
        if factory is None:
            available = ", ".join(self._engines.keys()) or "(none)"
            raise ValueError(
                f'Context engine "{eid}" not registered. Available: {available}'
            )
        result = factory()
        if hasattr(result, "__await__"):
            return await result
        return result


_registry: Optional[ContextEngineRegistry] = None


def get_context_engine_registry() -> ContextEngineRegistry:
    global _registry
    if _registry is None:
        _registry = ContextEngineRegistry()
    return _registry
