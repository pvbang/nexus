"""
Memory Pipeline Scheduler.

Coordinates background processing:
- L1 extraction: Triggered by L0 message count threshold
- L2 aggregation: Triggered after L1 with delay
- L3 persona: Triggered by atom count threshold

All processing is non-blocking and fire-and-forget.
Failures are logged but never crash the main pipeline.
"""
from __future__ import annotations

import asyncio
import structlog

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.memory.layers.config import MemoryLayersConfig
from app.memory.layers.l0_capture import L0CaptureTracker
from app.memory.layers.l1_extraction import L1Extractor
from app.memory.layers.l2_scenes import L2SceneAggregator
from app.memory.layers.l3_persona import L3PersonaSynthesizer
from app.services.llm_client import LLMClient

logger = structlog.get_logger(__name__)


class MemoryPipelineScheduler:
    """Coordinates background memory processing pipeline."""

    def __init__(
        self,
        config: MemoryLayersConfig,
        llm_client: LLMClient,
        session_factory: async_sessionmaker,
    ):
        self.config = config
        self.llm = llm_client
        self.session_factory = session_factory

        # Layer instances
        self.l0 = L0CaptureTracker(config)
        self.l1 = L1Extractor(config, llm_client)
        self.l2 = L2SceneAggregator(config, llm_client)
        self.l3 = L3PersonaSynthesizer(config, llm_client)

        # Track running tasks to prevent double-scheduling
        self._running_sessions: set[str] = set()

    async def on_message_persisted(
        self,
        db: AsyncSession,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        """
        Called after each user message is persisted.
        Checks if L1 should trigger, and schedules background pipeline.
        """
        if not self.config.enabled:
            return

        should_trigger = await self.l0.on_message_persisted(
            db, session_id, agent_id, user_id
        )

        if should_trigger and session_id not in self._running_sessions:
            asyncio.create_task(
                self._run_pipeline(session_id, agent_id, user_id),
                name=f"memory-pipeline-{session_id[:8]}",
            )

    async def _run_pipeline(
        self,
        session_id: str,
        agent_id: str | None,
        user_id: str | None,
    ) -> None:
        """
        Run the full L1→L2→L3 pipeline in background.
        Uses its own DB session (independent of request lifecycle).
        """
        if session_id in self._running_sessions:
            logger.debug("pipeline_already_running", session_id=session_id)
            return

        self._running_sessions.add(session_id)

        try:
            # Phase 1: L1 Extraction
            async with self.session_factory() as db:
                try:
                    result = await self.l1.extract_from_session(
                        db, session_id, agent_id, user_id
                    )
                    if result.messages_processed > 0:
                        await self.l0.mark_l1_complete(
                            db, session_id, result.messages_processed
                        )
                    await db.commit()

                    logger.info(
                        "pipeline_l1_done",
                        session_id=session_id,
                        atoms=len(result.atoms),
                        messages=result.messages_processed,
                    )
                except Exception as e:
                    await db.rollback()
                    logger.error(
                        "pipeline_l1_failed", session_id=session_id, error=str(e)
                    )
                    return  # Don't continue if L1 fails

            # Phase 2: L2 Scene Aggregation (with delay)
            await asyncio.sleep(self.config.l2_delay_after_l1_seconds)

            async with self.session_factory() as db:
                try:
                    scenes_count = await self.l2.aggregate_scenes(
                        db, session_id, agent_id, user_id
                    )
                    await db.commit()

                    if scenes_count > 0:
                        logger.info(
                            "pipeline_l2_done",
                            session_id=session_id,
                            scenes=scenes_count,
                        )
                except Exception as e:
                    await db.rollback()
                    logger.error(
                        "pipeline_l2_failed", session_id=session_id, error=str(e)
                    )

            # Phase 3: L3 Persona (if threshold met)
            async with self.session_factory() as db:
                try:
                    should_l3 = await self.l3.should_trigger(db, user_id, agent_id)
                    if should_l3:
                        persona = await self.l3.synthesize_persona(
                            db, session_id, agent_id, user_id
                        )
                        await db.commit()

                        if persona:
                            logger.info(
                                "pipeline_l3_done",
                                session_id=session_id,
                                persona_length=len(persona),
                            )
                    else:
                        await db.commit()
                except Exception as e:
                    await db.rollback()
                    logger.error(
                        "pipeline_l3_failed", session_id=session_id, error=str(e)
                    )

        finally:
            self._running_sessions.discard(session_id)

    async def force_run_pipeline(
        self,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> dict:
        """
        Force-run the full pipeline synchronously (for testing/admin).
        Returns summary of what was processed.
        """
        summary = {"l1_atoms": 0, "l2_scenes": 0, "l3_persona": False}

        async with self.session_factory() as db:
            result = await self.l1.extract_from_session(
                db, session_id, agent_id, user_id
            )
            summary["l1_atoms"] = len(result.atoms)
            if result.messages_processed > 0:
                await self.l0.mark_l1_complete(
                    db, session_id, result.messages_processed
                )
            await db.commit()

        async with self.session_factory() as db:
            count = await self.l2.aggregate_scenes(db, session_id, agent_id, user_id)
            summary["l2_scenes"] = count
            await db.commit()

        async with self.session_factory() as db:
            persona = await self.l3.synthesize_persona(
                db, session_id, agent_id, user_id
            )
            summary["l3_persona"] = persona is not None
            await db.commit()

        return summary
