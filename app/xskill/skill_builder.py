"""
MekongAI Nexus - XSkill Skill Builder.

Đúc kết Skills (task-level workflows) từ các task hoàn thành.
Skills = chuỗi các bước + tool phối hợp đã chứng minh hiệu quả.

Optimizations:
- Pre-creation deduplication: check if similar skill exists before INSERT
- Relevance threshold on retrieval: no irrelevant fallback
- Skill consolidation: periodic merge/prune
"""

import uuid
from typing import Any, Optional

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import SkillModel
from app.services.llm_client import LLMClient, get_core_llm

logger = structlog.get_logger(__name__)

# ── Deduplication thresholds ──
# Cosine distance: 0 = identical, 2 = opposite. < 0.25 = very similar.
_SKILL_DEDUP_DISTANCE_THRESHOLD = 0.25
# Relevance threshold for retrieval: only return skills with distance < this
_SKILL_RELEVANCE_THRESHOLD = 0.50


class SkillBuilder:
    """
    Xây dựng và quản lý Skills - workflow cấp task.

    Lấy cảm hứng từ XSkill skill_builder.py:
    - Generate skill từ trajectory thành công
    - Merge skills tương tự
    - Adapt skill cho task mới
    """

    def __init__(self, db: AsyncSession, llm: Optional[LLMClient] = None):
        self.db = db
        self.llm = llm or get_core_llm()

    async def build_skill(
        self,
        task_description: str,
        task_result: dict[str, Any],
        task_id: Optional[uuid.UUID] = None,
        agents_used: Optional[list[dict[str, str]]] = None,
    ) -> Optional[SkillModel]:
        """
        Phase 1: Đúc kết skill từ task hoàn thành thành công.

        Chỉ tạo skill khi task completed (không tạo từ failed tasks).
        Includes pre-creation deduplication: if similar skill exists, UPDATE it
        instead of creating a new one.
        """
        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a Skill Builder. Analyze a completed task and extract a "
                    "reusable workflow/skill. Return JSON with:\n"
                    "- title: concise skill name\n"
                    "- task_type: category (research, analysis, writing, coding, planning, etc.)\n"
                    "- content: detailed step-by-step workflow in markdown format:\n"
                    "  1. Step description (tool/agent role)\n"
                    "  2. ...\n"
                    "- tools_used: list of tool names used in this workflow\n"
                    "- effectiveness_score: 0.0-1.0 estimated effectiveness\n"
                    "\nRules:\n"
                    "- Make the workflow reusable for similar future tasks\n"
                    "- Include specific tool recommendations\n"
                    "- Note the agent roles that worked well together\n"
                    "- Be concise but complete"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Task: {task_description}\n\n"
                    f"Strategy: {task_result.get('strategy', 'unknown')}\n"
                    f"Rounds: {task_result.get('rounds', 'N/A')}\n"
                    f"Agents: {agents_used or 'N/A'}\n"
                    f"Final Answer Preview: {str(task_result.get('final_answer', ''))[:500]}\n"
                    f"Reasoning: {task_result.get('reasoning', 'N/A')}"
                ),
            },
        ]

        try:
            result = await self.llm.chat_json(prompt, temperature=0.3, max_tokens=8192)
        except Exception as e:
            logger.warning("skill_building_failed", error=str(e))
            return None

        skill = SkillModel(
            title=result.get("title", f"Skill for: {task_description[:50]}"),
            content=result.get("content", ""),
            task_type=result.get("task_type", "general"),
            source_task_id=task_id,
            tools_used=result.get("tools_used", []),
            effectiveness_score=result.get("effectiveness_score", 0.5),
            metadata_={
                "agents_used": agents_used or [],
                "strategy": task_result.get("strategy"),
            },
        )

        # G2: Generate embedding for semantic search (best-effort)
        try:
            from app.xskill.embedding import generate_embedding

            embed_text = f"{skill.title} {skill.task_type} {skill.content[:500]}"
            embedding = await generate_embedding(embed_text)
            if embedding:
                skill.embedding = embedding
        except Exception:
            pass  # Embedding is optional — ILIKE fallback always works

        # ── Tier 2: Pre-creation Deduplication ──
        # Check if a very similar skill already exists. If so, UPDATE it instead.
        existing = await self._find_duplicate_skill(skill)
        if existing:
            # Update existing skill: increment success_count, optionally improve content
            existing.success_count += 1
            # If new skill has higher effectiveness, update content
            if skill.effectiveness_score > existing.effectiveness_score:
                existing.content = skill.content
                existing.effectiveness_score = skill.effectiveness_score
                existing.tools_used = skill.tools_used
            # Merge metadata
            if existing.metadata_ and skill.metadata_:
                existing.metadata_ = {**existing.metadata_, **skill.metadata_}
            await self.db.flush()
            logger.info(
                "skill_deduplicated_updated_existing",
                existing_id=str(existing.id),
                title=existing.title,
                new_success_count=existing.success_count,
            )
            return existing

        # No duplicate — INSERT new skill
        self.db.add(skill)
        await self.db.flush()

        logger.info(
            "skill_built",
            skill_id=str(skill.id),
            title=skill.title,
            task_type=skill.task_type,
        )
        return skill

    async def _find_duplicate_skill(self, new_skill: SkillModel) -> Optional[SkillModel]:
        """
        Pre-creation deduplication: find existing skill that is very similar.

        Strategy:
        1. Vector similarity (cosine distance < threshold) if embeddings available
        2. Text match fallback: same task_type + title prefix overlap

        Returns existing SkillModel if duplicate found, None otherwise.
        """
        # Strategy A: Vector similarity (most accurate)
        if new_skill.embedding is not None:
            try:
                from sqlalchemy import literal_column

                result = await self.db.execute(
                    select(
                        SkillModel,
                        SkillModel.embedding.cosine_distance(new_skill.embedding).label("distance"),
                    )
                    .where(SkillModel.embedding.isnot(None))
                    .order_by(literal_column("distance"))
                    .limit(1)
                )
                row = result.first()
                if row and row.distance < _SKILL_DEDUP_DISTANCE_THRESHOLD:
                    logger.debug(
                        "skill_dedup_vector_match",
                        existing_title=row.SkillModel.title[:60],
                        distance=round(row.distance, 4),
                    )
                    return row.SkillModel
            except Exception as vec_err:
                logger.debug("skill_dedup_vector_failed", error=str(vec_err))

        # Strategy B: Text match fallback (same task_type + similar title)
        title_prefix = new_skill.title[:40].strip()
        if len(title_prefix) > 10:
            try:
                result = await self.db.execute(
                    select(SkillModel)
                    .where(
                        SkillModel.task_type == new_skill.task_type,
                        SkillModel.title.ilike(f"{title_prefix}%"),
                    )
                    .limit(1)
                )
                existing = result.scalar_one_or_none()
                if existing:
                    logger.debug(
                        "skill_dedup_text_match",
                        existing_title=existing.title[:60],
                        new_title=new_skill.title[:60],
                    )
                    return existing
            except Exception:
                pass

        return None

    async def retrieve_relevant_skills(
        self,
        task_description: str,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """
        Phase 2: Retrieve skills phù hợp cho task mới.

        Strategy (optimized):
        1. Try vector search with RELEVANCE THRESHOLD (cosine distance < 0.50)
        2. Fallback to multi-word ILIKE search (keyword relevance)
        3. NO irrelevant fallback — empty is better than noise

        Key change: removed "top by effectiveness" fallback that returned
        completely irrelevant skills. No knowledge > wrong knowledge.
        """
        skills: list[Any] = []

        # Vector search with relevance threshold
        try:
            from app.xskill.embedding import generate_embedding
            from sqlalchemy import literal_column

            query_embedding = await generate_embedding(task_description)
            if query_embedding:
                result = await self.db.execute(
                    select(
                        SkillModel,
                        SkillModel.embedding.cosine_distance(query_embedding).label("distance"),
                    )
                    .where(SkillModel.embedding.isnot(None))
                    .order_by(literal_column("distance"))
                    .limit(limit * 2)  # Fetch extra, then filter by threshold
                )
                rows = result.all()
                # Only keep skills within relevance threshold
                skills = [
                    row.SkillModel for row in rows
                    if row.distance < _SKILL_RELEVANCE_THRESHOLD
                ][:limit]
                if skills:
                    logger.debug(
                        "skill_vector_search_hit",
                        count=len(skills),
                        best_distance=round(rows[0].distance, 4) if rows else None,
                    )
                elif rows:
                    logger.debug(
                        "skill_vector_search_no_relevant",
                        best_distance=round(rows[0].distance, 4),
                        threshold=_SKILL_RELEVANCE_THRESHOLD,
                    )
        except Exception as vec_err:
            logger.debug("skill_vector_search_failed", error=str(vec_err))

        # Fallback: ILIKE keyword search (still relevant — keywords must match)
        if not skills:
            from sqlalchemy import or_

            words = [w for w in task_description.lower().split() if len(w) > 2][:5]

            if words:
                conditions = []
                for word in words:
                    conditions.append(SkillModel.content.ilike(f"%{word}%"))
                    conditions.append(SkillModel.title.ilike(f"%{word}%"))
                    conditions.append(SkillModel.task_type.ilike(f"%{word}%"))

                result = await self.db.execute(
                    select(SkillModel)
                    .where(or_(*conditions))
                    .order_by(
                        SkillModel.effectiveness_score.desc(),
                        SkillModel.success_count.desc(),
                    )
                    .limit(limit)
                )
                skills = list(result.scalars().all())

        # NO final fallback — returning irrelevant skills is WORSE than returning nothing.
        # Previously: "top skills by effectiveness" returned completely unrelated skills.

        return [
            {
                "id": str(s.id),
                "title": s.title,
                "task_type": s.task_type,
                "content": s.content,
                "tools_used": s.tools_used,
                "effectiveness": s.effectiveness_score,
                "success_count": s.success_count,
            }
            for s in skills
        ]

    async def adapt_skill_for_task(
        self,
        skill: dict[str, Any],
        task_description: str,
    ) -> str:
        """Adapt một skill có sẵn cho task mới."""
        prompt = [
            {
                "role": "system",
                "content": (
                    "Adapt the following workflow/skill for a new task. "
                    "Return the adapted workflow as plain text (markdown steps). "
                    "Adjust steps, tools, and strategies as needed for the new context."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original Skill: {skill.get('title', '')}\n"
                    f"Workflow:\n{skill.get('content', '')}\n\n"
                    f"New Task: {task_description}"
                ),
            },
        ]
        result = await self.llm.chat(prompt, temperature=0.5)
        return result.get("content", skill.get("content", ""))

    async def update_effectiveness(
        self,
        skill_id: uuid.UUID,
        was_successful: bool,
    ):
        """Cập nhật effectiveness score sau khi skill được sử dụng."""
        if was_successful:
            await self.db.execute(
                update(SkillModel)
                .where(SkillModel.id == skill_id)
                .values(success_count=SkillModel.success_count + 1)
            )
        else:
            await self.db.execute(
                update(SkillModel)
                .where(SkillModel.id == skill_id)
                .values(fail_count=SkillModel.fail_count + 1)
            )

    async def list_all(self, limit: int = 50) -> list[dict[str, Any]]:
        """Liệt kê tất cả skills."""
        result = await self.db.execute(
            select(SkillModel)
            .order_by(SkillModel.effectiveness_score.desc())
            .limit(limit)
        )
        return [
            {
                "id": str(s.id),
                "title": s.title,
                "task_type": s.task_type,
                "content": s.content,  # Full content for frontend display
                "tools_used": s.tools_used,
                "effectiveness": s.effectiveness_score,
                "success_count": s.success_count,
                "fail_count": s.fail_count,
                "source_task_id": str(s.source_task_id) if s.source_task_id else None,
                "metadata": s.metadata_,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in result.scalars().all()
        ]

    async def consolidate_skills(self, max_merges: int = 10) -> dict[str, Any]:
        """
        Tier 4: Periodic skill consolidation.

        Operations:
        1. Merge very similar skills (vector distance < dedup threshold)
        2. Prune low-quality skills (fail_count > success_count * 2 AND success_count < 3)
        3. Return summary of actions taken.

        Should be called periodically (e.g., after every 10 tasks, or via admin API).
        """
        from sqlalchemy import text, delete, literal_column

        merged_count = 0
        pruned_count = 0

        # ── Step 1: Prune low-quality skills ──
        # Skills that consistently fail and have never been truly successful
        try:
            prune_result = await self.db.execute(
                delete(SkillModel)
                .where(
                    SkillModel.fail_count > SkillModel.success_count * 2,
                    SkillModel.success_count < 3,
                    SkillModel.fail_count >= 3,
                )
            )
            pruned_count = prune_result.rowcount or 0
            if pruned_count:
                logger.info("skills_pruned", count=pruned_count)
        except Exception as prune_err:
            logger.debug("skill_prune_failed", error=str(prune_err))

        # ── Step 2: Merge very similar skills ──
        # Get all skills with embeddings, find pairs that are too close
        try:
            all_skills_result = await self.db.execute(
                select(SkillModel)
                .where(SkillModel.embedding.isnot(None))
                .order_by(SkillModel.success_count.desc())
                .limit(100)  # Process top 100 by success
            )
            all_skills = list(all_skills_result.scalars().all())

            # For each skill, find near-duplicates
            processed_ids: set[uuid.UUID] = set()
            for skill in all_skills:
                if merged_count >= max_merges:
                    break
                if skill.id in processed_ids:
                    continue

                # Find similar skills (not self)
                try:
                    near_result = await self.db.execute(
                        select(
                            SkillModel,
                            SkillModel.embedding.cosine_distance(skill.embedding).label("distance"),
                        )
                        .where(
                            SkillModel.id != skill.id,
                            SkillModel.embedding.isnot(None),
                            SkillModel.id.notin_(processed_ids) if processed_ids else True,
                        )
                        .order_by(literal_column("distance"))
                        .limit(3)
                    )
                    near_rows = near_result.all()
                except Exception:
                    continue

                for row in near_rows:
                    if row.distance >= _SKILL_DEDUP_DISTANCE_THRESHOLD:
                        break  # No more near-duplicates
                    if merged_count >= max_merges:
                        break

                    dup = row.SkillModel
                    # Merge: keep the one with higher success_count
                    keeper = skill if skill.success_count >= dup.success_count else dup
                    victim = dup if keeper is skill else skill

                    keeper.success_count += victim.success_count
                    keeper.fail_count += victim.fail_count
                    # Keep higher effectiveness
                    keeper.effectiveness_score = max(
                        keeper.effectiveness_score, victim.effectiveness_score
                    )

                    await self.db.delete(victim)
                    processed_ids.add(victim.id)
                    processed_ids.add(keeper.id)
                    merged_count += 1

            if merged_count > 0:
                await self.db.flush()
                logger.info("skills_consolidated", merged=merged_count)
        except Exception as merge_err:
            logger.warning("skill_consolidation_merge_failed", error=str(merge_err))

        return {
            "merged": merged_count,
            "pruned": pruned_count,
            "total_actions": merged_count + pruned_count,
        }

    async def get_stats(self) -> dict[str, Any]:
        """Thống kê skills."""
        from sqlalchemy import func, text

        total = await self.db.execute(text("SELECT COUNT(*) FROM skills"))
        avg_eff = await self.db.execute(
            text("SELECT AVG(effectiveness_score) FROM skills")
        )
        types = await self.db.execute(
            text("SELECT task_type, COUNT(*) FROM skills GROUP BY task_type")
        )

        return {
            "total_skills": total.scalar() or 0,
            "avg_effectiveness": round(float(avg_eff.scalar() or 0), 3),
            "by_type": {row[0]: row[1] for row in types.all()},
        }
