"""
Memory Layers System — Four-layer memory inspired by TencentDB Agent Memory.

L0: Conversation Capture (raw dialogue tracking)
L1: Atom Extraction (structured facts via LLM)
L2: Scene Aggregation (cluster atoms into scene blocks)
L3: Persona Synthesis (user/agent profile generation)

Usage:
    from app.memory.layers import MemoryLayerRecall, MemoryPipelineScheduler

    # Recall (before each turn):
    recall = MemoryLayerRecall(config)
    response = await recall.recall(db, query, user_id=user_id)
    context = recall.format_for_injection(response)

    # Pipeline (after each message):
    scheduler = MemoryPipelineScheduler(config, llm, session_factory)
    await scheduler.on_message_persisted(db, session_id)
"""
from app.memory.layers.config import MemoryLayersConfig, LayerTokenBudget
from app.memory.layers.models import (
    AtomType,
    MemoryAtomModel,
    MemorySceneModel,
    MemoryPersonaModel,
    MemoryPipelineStateModel,
    MemoryAtom,
    MemoryAtomResponse,
    RecallResult,
    RecallResponse,
    L1ExtractionResult,
    PipelineState,
)
from app.memory.layers.l0_capture import L0CaptureTracker
from app.memory.layers.l1_extraction import L1Extractor
from app.memory.layers.l2_scenes import L2SceneAggregator
from app.memory.layers.l3_persona import L3PersonaSynthesizer
from app.memory.layers.recall import MemoryLayerRecall
from app.memory.layers.scheduler import MemoryPipelineScheduler

__all__ = [
    "MemoryLayersConfig",
    "LayerTokenBudget",
    "AtomType",
    "MemoryAtomModel",
    "MemorySceneModel",
    "MemoryPersonaModel",
    "MemoryPipelineStateModel",
    "MemoryAtom",
    "MemoryAtomResponse",
    "RecallResult",
    "RecallResponse",
    "L1ExtractionResult",
    "PipelineState",
    "L0CaptureTracker",
    "L1Extractor",
    "L2SceneAggregator",
    "L3PersonaSynthesizer",
    "MemoryLayerRecall",
    "MemoryPipelineScheduler",
]
