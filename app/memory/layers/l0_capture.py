"""
L0: Conversation Capture Layer.

Tracks message count per session and triggers L1 extraction when thresholds are met.
Raw messages are already persisted in `conversation_messages` table by the orchestrator.
This layer adds:
- Message count tracking per session (memory_pipeline_state)
- Threshold detection for triggering L1
- Warmup logic (1→2→4→...→N turns before first full trigger)
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.layers.models import MemoryPipelineStateModel
from app.memory.layers.config import MemoryLayersConfig

logger = structlog.get_logger(__name__)


class L0CaptureTracker:
    """Tracks conversation message counts and determines when to trigger L1."""

    def __init__(self, config: MemoryLayersConfig):
        self.config = config
        self._warmup_thresholds: dict[str, int] = {}  # session_id → current threshold

    async def on_message_persisted(
        self,
        db: AsyncSession,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> bool:
        """
        Called after each user message is persisted.
        Returns True if L1 extraction should be triggered.
        """
        state = await self._get_or_create_state(db, session_id, agent_id, user_id)
        new_count = state.message_count + 1

        # Update count
        await db.execute(
            update(MemoryPipelineStateModel)
            .where(MemoryPipelineStateModel.session_id == session_id)
            .values(message_count=new_count, updated_at=datetime.now(timezone.utc))
        )
        await db.flush()

        # Check if L1 should trigger
        should_trigger = self._should_trigger_l1(session_id, new_count, state.l1_cursor)

        if should_trigger:
            logger.info(
                "l0_trigger_l1",
                session_id=session_id,
                message_count=new_count,
                l1_cursor=state.l1_cursor,
            )

        return should_trigger

    def _should_trigger_l1(
        self, session_id: str, message_count: int, l1_cursor: int
    ) -> bool:
        """Determine if L1 extraction should run based on message count."""
        messages_since_l1 = message_count - l1_cursor

        if self.config.l1_warmup_enabled:
            # Warmup: start at 1, double each time (1→2→4→...→N)
            threshold = self._warmup_thresholds.get(session_id, 1)
            if messages_since_l1 >= threshold:
                # Double threshold for next time, cap at configured value
                self._warmup_thresholds[session_id] = min(
                    threshold * 2, self.config.l1_every_n_turns
                )
                return True
            return False
        else:
            return messages_since_l1 >= self.config.l1_every_n_turns

    async def _get_or_create_state(
        self,
        db: AsyncSession,
        session_id: str,
        agent_id: str | None,
        user_id: str | None,
    ) -> MemoryPipelineStateModel:
        """Get or create pipeline state for session."""
        stmt = select(MemoryPipelineStateModel).where(
            MemoryPipelineStateModel.session_id == session_id
        )
        result = await db.execute(stmt)
        state = result.scalar_one_or_none()

        if state is None:
            state = MemoryPipelineStateModel(
                session_id=session_id,
                agent_id=agent_id,
                user_id=user_id,
                message_count=0,
                l1_cursor=0,
                l2_cursor=0,
                l3_cursor=0,
            )
            db.add(state)
            await db.flush()

        return state

    async def mark_l1_complete(
        self, db: AsyncSession, session_id: str, messages_processed: int
    ) -> None:
        """Update L1 cursor after successful extraction."""
        await db.execute(
            update(MemoryPipelineStateModel)
            .where(MemoryPipelineStateModel.session_id == session_id)
            .values(
                l1_cursor=MemoryPipelineStateModel.l1_cursor + messages_processed,
                last_l1_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()
