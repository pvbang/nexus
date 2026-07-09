"""
Memory Layers Startup — Initialize bridge at application startup.

Called from app/main.py or wherever the app initializes services.
"""
from __future__ import annotations

import structlog
from typing import Optional

from app.config import get_settings
from app.memory.layers.config import MemoryLayersConfig
from app.memory.layers.bridge import init_memory_layers_bridge, MemoryLayersBridge

logger = structlog.get_logger(__name__)


async def setup_memory_layers(
    llm_client,
    session_factory,
) -> Optional[MemoryLayersBridge]:
    """
    Initialize memory layers system.
    Call this during app startup after DB and LLM client are ready.

    Returns the bridge instance, or None if disabled.
    """
    settings = get_settings()

    if not getattr(settings, "memory_layers_enabled", True):
        logger.info("memory_layers_disabled")
        return None

    config = MemoryLayersConfig.from_app_config(settings)

    bridge = init_memory_layers_bridge(
        config=config,
        llm_client=llm_client,
        session_factory=session_factory,
    )

    logger.info(
        "memory_layers_ready",
        recall_strategy=config.recall_strategy,
        l1_every_n=config.l1_every_n_turns,
        l3_trigger_n=config.l3_trigger_every_n_atoms,
    )

    return bridge
