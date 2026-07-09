"""
L2: Scene Aggregation Layer.

Clusters L1 atoms into coherent scene blocks.
Each scene is a Markdown document grouping related memories by theme.
"""
from __future__ import annotations

import json
import structlog
from datetime import datetime, timezone

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.layers.models import (
    MemoryAtomModel,
    MemorySceneModel,
    MemoryPipelineStateModel,
)
from app.memory.layers.config import MemoryLayersConfig
from app.memory.layers.prompts import L2_SCENE_SYSTEM, L2_SCENE_USER
from app.services.llm_client import LLMClient

logger = structlog.get_logger(__name__)


class L2SceneAggregator:
    """Aggregates L1 atoms into scene blocks."""

    def __init__(self, config: MemoryLayersConfig, llm_client: LLMClient):
        self.config = config
        self.llm = llm_client

    async def aggregate_scenes(
        self,
        db: AsyncSession,
        session_id: str,
        agent_id: str | None = None,
        user_id: str | None = None,
    ) -> int:
        """
        Run scene aggregation for a session.
        Returns number of scenes created/updated.
        """
        try:
            # Get pipeline state
            state = await self._get_state(db, session_id)
            if not state:
                logger.warning("l2_no_state", session_id=session_id)
                return 0

            # Get unprocessed atoms (since last L2)
            atoms = await self._fetch_new_atoms(db, user_id, agent_id, state.last_l2_at)
            if not atoms:
                logger.debug("l2_no_new_atoms", session_id=session_id)
                return 0

            # Get existing scenes
            existing_scenes = await self._fetch_existing_scenes(db, user_id, agent_id)

            # Call LLM for scene clustering
            scene_data = await self._llm_aggregate(atoms, existing_scenes)
            if not scene_data:
                return 0

            # Persist scenes
            count = await self._save_scenes(db, scene_data, agent_id, user_id)

            # Enforce max scenes limit
            await self._enforce_max_scenes(db, user_id, agent_id)

            # Update pipeline state
            await db.execute(
                update(MemoryPipelineStateModel)
                .where(MemoryPipelineStateModel.session_id == session_id)
                .values(
                    l2_cursor=state.l2_cursor + len(atoms),
                    last_l2_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            await db.flush()

            logger.info(
                "l2_aggregation_complete",
                session_id=session_id,
                atoms_processed=len(atoms),
                scenes_saved=count,
            )
            return count

        except Exception as e:
            logger.error(
                "l2_aggregation_failed",
                session_id=session_id,
                error=str(e),
                exc_info=True,
            )
            return 0

    async def _get_state(
        self, db: AsyncSession, session_id: str
    ) -> MemoryPipelineStateModel | None:
        """Get pipeline state for session."""
        stmt = select(MemoryPipelineStateModel).where(
            MemoryPipelineStateModel.session_id == session_id
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _fetch_new_atoms(
        self,
        db: AsyncSession,
        user_id: str | None,
        agent_id: str | None,
        since: datetime | None,
    ) -> list[MemoryAtomModel]:
        """Fetch atoms created after last L2 run."""
        stmt = select(MemoryAtomModel).where(
            MemoryAtomModel.is_active == True,  # noqa: E712
        )
        if user_id:
            stmt = stmt.where(MemoryAtomModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemoryAtomModel.agent_id == agent_id)
        if since:
            stmt = stmt.where(MemoryAtomModel.created_at > since)
        stmt = stmt.order_by(MemoryAtomModel.created_at.asc()).limit(100)

        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def _fetch_existing_scenes(
        self, db: AsyncSession, user_id: str | None, agent_id: str | None
    ) -> list[MemorySceneModel]:
        """Fetch active scenes for context."""
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

    async def _llm_aggregate(
        self,
        atoms: list[MemoryAtomModel],
        existing_scenes: list[MemorySceneModel],
    ) -> list[dict]:
        """Call LLM to cluster atoms into scenes."""
        atoms_text = "\n".join(
            f"- [{atom.atom_type.value}] (id={atom.id}, importance={atom.importance:.1f}) {atom.content}"
            for atom in atoms
        )

        scenes_text = (
            "\n".join(
                f"- (id={scene.id}) **{scene.title}**: {scene.content_md[:200]}..."
                for scene in existing_scenes[: self.config.l2_max_scenes]
            )
            if existing_scenes
            else "(none)"
        )

        user_prompt = (
            L2_SCENE_USER
            .replace("{atoms}", atoms_text)
            .replace("{existing_scenes}", scenes_text)
            .replace("{max_scenes}", str(self.config.l2_max_scenes))
        )

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": L2_SCENE_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=3000,
            )
            content = (
                response.get("content", "") if isinstance(response, dict) else str(response)
            )
            return self._parse_json_array(content)
        except Exception as e:
            logger.error("l2_llm_call_failed", error=str(e))
            return []

    async def _save_scenes(
        self,
        db: AsyncSession,
        scene_data: list[dict],
        agent_id: str | None,
        user_id: str | None,
    ) -> int:
        """Save or merge scenes into database."""
        count = 0
        for item in scene_data:
            title = item.get("title", "Untitled Scene")
            content_md = item.get("content_md", "")
            atom_ids = item.get("atom_ids", [])
            merge_with = item.get("should_merge_with")

            if not content_md:
                continue

            if merge_with:
                # Update existing scene
                await db.execute(
                    update(MemorySceneModel)
                    .where(MemorySceneModel.id == merge_with)
                    .values(
                        content_md=content_md,
                        title=title,
                        atom_count=MemorySceneModel.atom_count + len(atom_ids),
                        updated_at=datetime.now(timezone.utc),
                    )
                )
            else:
                # Create new scene
                scene = MemorySceneModel(
                    agent_id=agent_id,
                    user_id=user_id,
                    title=title,
                    content_md=content_md,
                    atom_ids=atom_ids,
                    atom_count=len(atom_ids),
                )
                db.add(scene)
            count += 1

        await db.flush()
        return count

    async def _enforce_max_scenes(
        self, db: AsyncSession, user_id: str | None, agent_id: str | None
    ) -> None:
        """Archive oldest scenes if over limit."""
        stmt = select(func.count(MemorySceneModel.id)).where(
            MemorySceneModel.is_active == True,  # noqa: E712
        )
        if user_id:
            stmt = stmt.where(MemorySceneModel.user_id == user_id)
        if agent_id:
            stmt = stmt.where(MemorySceneModel.agent_id == agent_id)

        result = await db.execute(stmt)
        total = result.scalar() or 0

        if total <= self.config.l2_max_scenes:
            return

        excess = total - self.config.l2_max_scenes
        oldest_stmt = (
            select(MemorySceneModel.id)
            .where(MemorySceneModel.is_active == True)  # noqa: E712
        )
        if user_id:
            oldest_stmt = oldest_stmt.where(MemorySceneModel.user_id == user_id)
        if agent_id:
            oldest_stmt = oldest_stmt.where(MemorySceneModel.agent_id == agent_id)
        oldest_stmt = oldest_stmt.order_by(
            MemorySceneModel.updated_at.asc()
        ).limit(excess)

        result = await db.execute(oldest_stmt)
        ids_to_archive = [row[0] for row in result.all()]

        if ids_to_archive:
            await db.execute(
                update(MemorySceneModel)
                .where(MemorySceneModel.id.in_(ids_to_archive))
                .values(is_active=False)
            )
            await db.flush()
            logger.info("l2_archived_scenes", count=len(ids_to_archive))

    def _parse_json_array(self, text: str) -> list[dict]:
        """Parse JSON array from LLM response, handling markdown code blocks."""
        text = text.strip()

        # Strip markdown code blocks
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Try direct parse
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            return []
        except json.JSONDecodeError:
            pass

        # Try to find JSON array in text
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass

        logger.warning("l2_json_parse_failed", text_preview=text[:200])
        return []
