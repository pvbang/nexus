"""
L3: Persona Synthesis Layer.

Generates a comprehensive user/agent persona profile from scene blocks.
The persona is a Markdown document summarizing:
- Personality, preferences, communication style
- Key instructions and standing rules
- Context and background
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone

from sqlalchemy import select, update, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.layers.models import (
    MemoryAtomModel,
    MemorySceneModel,
    MemoryPersonaModel,
    MemoryPipelineStateModel,
)
from app.memory.layers.config import MemoryLayersConfig
from app.memory.layers.prompts import L3_PERSONA_SYSTEM, L3_PERSONA_USER
from app.services.llm_client import LLMClient

logger = structlog.get_logger(__name__)


class L3PersonaSynthesizer:
    """Synthesizes user/agent persona from scenes."""

    def __init__(self, config: MemoryLayersConfig, llm_client: LLMClient):
        self.config = config
        self.llm = llm_client

    async def should_trigger(
        self,
        db: AsyncSession,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> bool:
        """Check if L3 should run based on atom count threshold."""
        atom_count = await self._get_atom_count(db, user_id, agent_id)
        last_persona = await self._get_latest_persona(db, user_id, agent_id)
        last_count = last_persona.based_on_atom_count if last_persona else 0

        return (atom_count - last_count) >= self.config.l3_trigger_every_n_atoms

    async def synthesize_persona(
        self,
        db: AsyncSession,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> str | None:
        """
        Generate or update persona profile.
        Returns the persona Markdown content, or None on failure.
        """
        try:
            # Get all active scenes
            scenes = await self._fetch_active_scenes(db, user_id, agent_id)
            if not scenes:
                logger.debug("l3_no_scenes", session_id=session_id)
                return None

            # Get previous persona for continuity
            previous_persona = await self._get_latest_persona(db, user_id, agent_id)
            previous_content = previous_persona.content_md if previous_persona else ""

            # Call LLM for persona synthesis
            persona_md = await self._llm_synthesize(scenes, previous_content)
            if not persona_md:
                return None

            # Get current atom count for tracking
            atom_count = await self._get_atom_count(db, user_id, agent_id)
            scene_ids = [str(s.id) for s in scenes]

            # Determine version
            new_version = (previous_persona.version + 1) if previous_persona else 1

            # Save new persona
            persona = MemoryPersonaModel(
                agent_id=agent_id,
                user_id=user_id,
                content_md=persona_md,
                version=new_version,
                based_on_scene_ids=scene_ids,
                based_on_atom_count=atom_count,
            )
            db.add(persona)

            # Enforce backup count (keep only N latest)
            await self._enforce_backup_limit(db, user_id, agent_id)

            # Update pipeline state
            await db.execute(
                update(MemoryPipelineStateModel)
                .where(MemoryPipelineStateModel.session_id == session_id)
                .values(
                    l3_cursor=atom_count,
                    last_l3_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()

            logger.info(
                "l3_persona_synthesized",
                session_id=session_id,
                version=new_version,
                scenes_used=len(scenes),
                atom_count=atom_count,
                content_length=len(persona_md),
            )
            return persona_md

        except Exception as e:
            logger.error(
                "l3_synthesis_failed",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            return None

    async def get_active_persona(
        self,
        db: AsyncSession,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> str | None:
        """Get the latest persona content for recall injection."""
        persona = await self._get_latest_persona(db, user_id, agent_id)
        return persona.content_md if persona else None

    async def _get_latest_persona(
        self, db: AsyncSession, user_id: str | None, agent_id: str | None
    ) -> MemoryPersonaModel | None:
        """Get the most recent persona version."""
        stmt = select(MemoryPersonaModel)
        if user_id:
            stmt = stmt.where(MemoryPersonaModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemoryPersonaModel.agent_id == agent_id)
        stmt = stmt.order_by(MemoryPersonaModel.version.desc()).limit(1)

        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _fetch_active_scenes(
        self, db: AsyncSession, user_id: str | None, agent_id: str | None
    ) -> list[MemorySceneModel]:
        """Fetch all active scenes for persona synthesis."""
        stmt = select(MemorySceneModel).where(
            MemorySceneModel.is_active == True,  # noqa: E712
        )
        if user_id:
            stmt = stmt.where(MemorySceneModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemorySceneModel.agent_id == agent_id)
        stmt = stmt.order_by(MemorySceneModel.updated_at.desc())

        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def _get_atom_count(
        self, db: AsyncSession, user_id: str | None, agent_id: str | None
    ) -> int:
        """Get total active atom count."""
        stmt = select(func.count(MemoryAtomModel.id)).where(
            MemoryAtomModel.is_active == True,  # noqa: E712
        )
        if user_id:
            stmt = stmt.where(MemoryAtomModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemoryAtomModel.agent_id == agent_id)

        result = await db.execute(stmt)
        return result.scalar() or 0

    async def _llm_synthesize(
        self, scenes: list[MemorySceneModel], previous_persona: str
    ) -> str | None:
        """Call LLM to synthesize persona from scenes."""
        scenes_text = "\n\n".join(
            f"### {scene.title}\n{scene.content_md}" for scene in scenes
        )

        user_prompt = (
            L3_PERSONA_USER
            .replace("{scenes}", scenes_text)
            .replace("{previous_persona}", previous_persona or "(No previous persona — generate from scratch)")
        )

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": L3_PERSONA_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            content = (
                response.get("content", "") if isinstance(response, dict) else str(response)
            )

            # Strip any code block wrappers
            content = content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                lines = [line for line in lines if not line.strip().startswith("```")]
                content = "\n".join(lines).strip()

            if len(content) < 50:
                logger.warning("l3_persona_too_short", length=len(content))
                return None

            return content

        except Exception as e:
            logger.error("l3_llm_call_failed", error=str(e))
            return None

    async def _enforce_backup_limit(
        self, db: AsyncSession, user_id: str | None, agent_id: str | None
    ) -> None:
        """Keep only the N most recent persona versions."""
        stmt = select(MemoryPersonaModel.id)
        if user_id:
            stmt = stmt.where(MemoryPersonaModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemoryPersonaModel.agent_id == agent_id)
        stmt = stmt.order_by(MemoryPersonaModel.version.desc())

        result = await db.execute(stmt)
        all_ids = [row[0] for row in result.all()]

        if len(all_ids) > self.config.l3_backup_count:
            ids_to_delete = all_ids[self.config.l3_backup_count :]
            await db.execute(
                delete(MemoryPersonaModel).where(
                    MemoryPersonaModel.id.in_(ids_to_delete)
                )
            )
            await db.flush()
            logger.debug("l3_cleaned_old_versions", deleted=len(ids_to_delete))
