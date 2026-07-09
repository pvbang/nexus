"""
Memory Layers Bridge — Integration point between Memory Layers and CoreOrchestrator.

This module provides a clean facade for the orchestrator to interact with
the four-layer memory system. It handles initialization, lifecycle, and
exposes two simple entry points:

1. on_user_message() — called after persisting user message (triggers pipeline)
2. get_recall_context() — called before agent turn (retrieves memory context)
"""
from __future__ import annotations

import structlog
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.memory.layers.config import MemoryLayersConfig
from app.memory.layers.recall import MemoryLayerRecall
from app.memory.layers.scheduler import MemoryPipelineScheduler
from app.services.llm_client import LLMClient

logger = structlog.get_logger(__name__)

# Module-level singleton (lazily initialized)
_instance: Optional["MemoryLayersBridge"] = None


class MemoryLayersBridge:
    """
    Facade for memory layers integration with orchestrator.

    Lifecycle:
    1. Call init() once at app startup (or lazily on first use)
    2. Call on_user_message() after each user message is persisted
    3. Call get_recall_context() before each agent turn
    """

    def __init__(
        self,
        config: MemoryLayersConfig,
        llm_client: LLMClient,
        session_factory: async_sessionmaker,
    ):
        self.config = config
        self.recall = MemoryLayerRecall(config)
        self.scheduler = MemoryPipelineScheduler(config, llm_client, session_factory)
        self._enabled = config.enabled
        logger.info("memory_layers_bridge_init", enabled=self._enabled)

    async def on_user_message(
        self,
        db: AsyncSession,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """
        Called after a user message is persisted to conversation_messages.
        Triggers L0 tracking and potentially schedules background pipeline.

        This is NON-BLOCKING — pipeline runs in background.
        """
        if not self._enabled:
            return

        try:
            await self.scheduler.on_message_persisted(
                db, session_id, agent_id, user_id
            )
        except Exception as e:
            # Never crash the main pipeline
            logger.error("memory_layers_on_message_error", error=str(e), session_id=session_id)

    async def get_recall_context(
        self,
        db: AsyncSession,
        query: str,
        session_id: str | None = None,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """
        Called before agent turn to retrieve relevant memory context.
        Returns formatted string for injection into system prompt.

        This respects timeout — returns empty string if takes too long.
        """
        if not self._enabled:
            return ""

        if not query or len(query.strip()) < 3:
            return ""

        try:
            response = await self.recall.recall(
                db, query, user_id=user_id, agent_id=agent_id, session_id=session_id
            )
            context = self.recall.format_for_injection(response)

            if context:
                logger.info(
                    "memory_layers_recall",
                    session_id=session_id,
                    results=len(response.results),
                    has_persona=bool(response.persona_snippet),
                    duration_ms=response.duration_ms,
                    strategy=response.strategy,
                    context_chars=len(context),
                )

            return context
        except Exception as e:
            logger.error("memory_layers_recall_error", error=str(e), session_id=session_id)
            return ""


def get_memory_layers_bridge() -> Optional[MemoryLayersBridge]:
    """Get the singleton bridge instance. Returns None if not initialized."""
    return _instance


def init_memory_layers_bridge(
    config: MemoryLayersConfig,
    llm_client: LLMClient,
    session_factory: async_sessionmaker,
) -> MemoryLayersBridge:
    """Initialize the singleton bridge instance."""
    global _instance
    _instance = MemoryLayersBridge(config, llm_client, session_factory)
    return _instance
