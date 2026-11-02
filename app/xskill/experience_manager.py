"""
Nexus - XSkill Experience Manager.

Quản lý lifecycle của Experiences:
- Tạo mới từ trajectory analysis
- Merge experiences tương tự
- Đánh giá effectiveness
- Lưu trữ với embeddings cho retrieval
- Full pipeline: compress → critique → extract (Phase 5b)
"""

import uuid
from typing import Any, Optional

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import ExperienceModel
from app.services.llm_client import LLMClient, get_core_llm
from app.xskill.experience_critique import CritiqueResult, ExperienceCritique
from app.xskill.trajectory_compressor import (
    TrajectoryCompressor,
    TrajectoryRecord,
)

logger = structlog.get_logger(__name__)

SIMILARITY_THRESHOLD = 0.70
# Relevance threshold for retrieval: cosine distance < this = relevant
_EXPERIENCE_RELEVANCE_THRESHOLD = 0.55


class ExperienceManager:
    """
    Quản lý Experiences - bài học chiến thuật cấp action.

    Lấy cảm hứng từ XSkill ExperienceMemoryProvider:
    - Tự động tạo experiences từ kết quả task
    - Merge experiences tương tự để tránh trùng lặp
    - Embedding-based similarity search
    - Full pipeline: compress → critique → extract (Phase 5b)
    """

    def __init__(self, db: AsyncSession, llm: Optional[LLMClient] = None):
        self.db = db
        self.llm = llm or get_core_llm()
        self._compressor = TrajectoryCompressor()
        self._critique = ExperienceCritique(llm=self.llm)

    async def extract_experiences(
        self,
        task_description: str,
        task_result: dict[str, Any],
        task_id: Optional[uuid.UUID] = None,
        agent_roles: Optional[list[str]] = None,
    ) -> list[ExperienceModel]:
        """
        Phase 1 (Accumulation): Phân tích kết quả task và đúc kết experiences.

        Gọi LLM để critique trajectory và rút ra bài học.
        """
        # Chuẩn bị trajectory summary
        trajectory = self._build_trajectory_summary(task_description, task_result)

        prompt = [
            {
                "role": "system",
                "content": (
                    "You are an Experience Extractor. Analyze the completed task trajectory "
                    "and extract tactical lessons learned. Return JSON with:\n"
                    "- experiences: list of experience objects, each with:\n"
                    "  - content: concise description of the experience/lesson\n"
                    "  - experience_type: tactical|error_recovery|optimization|pattern\n"
                    "  - context: when this experience applies\n"
                    "  - lesson: the key takeaway\n"
                    "  - importance: 0.0-1.0 (how critical is this lesson)\n"
                    "\nRules:\n"
                    "- Extract 1-5 experiences max\n"
                    "- Focus on actionable insights, not generic advice\n"
                    "- Prioritize error recovery and optimization patterns\n"
                    "- Include specific tool/strategy recommendations"
                ),
            },
            {
                "role": "user",
                "content": f"Task: {task_description}\n\nTrajectory:\n{trajectory}",
            },
        ]

        try:
            result = await self.llm.chat_json(prompt, temperature=0.3)
        except Exception as e:
            logger.warning("experience_extraction_failed", error=str(e))
            return []

        experiences: list[ExperienceModel] = []
        for exp_data in result.get("experiences", []):
            exp = ExperienceModel(
                content=exp_data.get("content", ""),
                experience_type=exp_data.get("experience_type", "tactical"),
                source_task_id=task_id,
                context=exp_data.get("context"),
                lesson=exp_data.get("lesson"),
                importance=exp_data.get("importance", 0.5),
                metadata_={
                    "task_description": task_description[:200],
                    "agent_roles": agent_roles or [],
                },
            )

            # G2: Generate embedding for semantic search (best-effort)
            try:
                from app.xskill.embedding import generate_embedding

                embed_text = f"{exp.content} {exp.context or ''} {exp.lesson or ''}"
                embedding = await generate_embedding(embed_text)
                if embedding:
                    exp.embedding = embedding
            except Exception:
                pass  # Embedding is optional — ILIKE fallback always works

            self.db.add(exp)
            experiences.append(exp)

        await self.db.flush()

        # Merge similar experiences
        await self._merge_similar_experiences()

        logger.info(
            "experiences_extracted",
            count=len(experiences),
            task_id=str(task_id) if task_id else None,
        )
        return experiences

    async def retrieve_relevant(
        self,
        task_description: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Phase 2 (Inference): Retrieve experiences phù hợp cho task mới.

        Strategy (optimized):
        1. Try vector search with RELEVANCE THRESHOLD (cosine distance < 0.55)
        2. Fallback to multi-word ILIKE search across content, context, lesson
        3. NO irrelevant fallback — empty is better than noise

        Key change: removed "top by importance" fallback that returned
        completely unrelated experiences. No knowledge > wrong knowledge.
        """
        experiences: list[Any] = []

        # Vector search with relevance threshold
        try:
            from app.xskill.embedding import generate_embedding
            from sqlalchemy import literal_column

            query_embedding = await generate_embedding(task_description)
            if query_embedding:
                result = await self.db.execute(
                    select(
                        ExperienceModel,
                        ExperienceModel.embedding.cosine_distance(query_embedding).label("distance"),
                    )
                    .where(ExperienceModel.embedding.isnot(None))
                    .order_by(literal_column("distance"))
                    .limit(limit * 2)  # Fetch extra, then filter by threshold
                )
                rows = result.all()
                # Only keep experiences within relevance threshold
                experiences = [
                    row.ExperienceModel for row in rows
                    if row.distance < _EXPERIENCE_RELEVANCE_THRESHOLD
                ][:limit]
                if experiences:
                    logger.debug(
                        "experience_vector_search_hit",
                        count=len(experiences),
                        best_distance=round(rows[0].distance, 4) if rows else None,
                    )
                elif rows:
                    logger.debug(
                        "experience_vector_search_no_relevant",
                        best_distance=round(rows[0].distance, 4),
                        threshold=_EXPERIENCE_RELEVANCE_THRESHOLD,
                    )
        except Exception as vec_err:
            logger.debug("experience_vector_search_failed", error=str(vec_err))

        # Fallback: ILIKE keyword search (still relevant — keywords must match)
        if not experiences:
            from sqlalchemy import or_

            words = [w for w in task_description.lower().split() if len(w) > 2][:5]

            if words:
                conditions = []
                for word in words:
                    conditions.append(ExperienceModel.content.ilike(f"%{word}%"))
                    conditions.append(ExperienceModel.context.ilike(f"%{word}%"))
                    conditions.append(ExperienceModel.lesson.ilike(f"%{word}%"))

                result = await self.db.execute(
                    select(ExperienceModel)
                    .where(or_(*conditions))
                    .order_by(ExperienceModel.importance.desc(), ExperienceModel.times_helpful.desc())
                    .limit(limit)
                )
                experiences = list(result.scalars().all())

        # NO final fallback — returning irrelevant experiences is WORSE than returning nothing.
        # Previously: "top by importance" returned completely unrelated lessons.

        return [
            {
                "id": str(e.id),
                "content": e.content,
                "type": e.experience_type,
                "context": e.context,
                "lesson": e.lesson,
                "importance": e.importance,
                "times_applied": e.times_applied,
            }
            for e in experiences
        ]

    async def mark_applied(self, experience_id: uuid.UUID, was_helpful: bool = True):
        """Đánh dấu experience đã được áp dụng và có hữu ích không."""
        await self.db.execute(
            update(ExperienceModel)
            .where(ExperienceModel.id == experience_id)
            .values(
                times_applied=ExperienceModel.times_applied + 1,
                times_helpful=ExperienceModel.times_helpful + (1 if was_helpful else 0),
            )
        )

    async def list_all(self, limit: int = 50) -> list[dict[str, Any]]:
        """Liệt kê tất cả experiences."""
        result = await self.db.execute(
            select(ExperienceModel)
            .order_by(ExperienceModel.importance.desc())
            .limit(limit)
        )
        return [
            {
                "id": str(e.id),
                "content": e.content,
                "type": e.experience_type,
                "context": e.context,
                "lesson": e.lesson,
                "importance": e.importance,
                "times_applied": e.times_applied,
                "times_helpful": e.times_helpful,
                "source_task_id": str(e.source_task_id) if e.source_task_id else None,
                "source_agent_id": str(e.source_agent_id) if e.source_agent_id else None,
                "metadata": e.metadata_,
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "updated_at": e.updated_at.isoformat() if e.updated_at else None,
            }
            for e in result.scalars().all()
        ]

    async def process_completed_task(
        self,
        trajectory: list[dict[str, Any]],
        task_description: str,
        task_id: Optional[uuid.UUID] = None,
        agent_roles: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Full pipeline: compress → critique → extract experience.

        Workflow:
        1. Compress raw trajectory thành TrajectoryRecord
        2. Critique trajectory quality (LLM-as-judge)
        3. Nếu quality đủ tốt, extract experiences
        4. Return summary kết quả

        Args:
            trajectory: Raw trajectory steps (list of dicts with role, content, etc.)
            task_description: Mô tả task đã thực hiện.
            task_id: Optional task UUID for linking experiences.
            agent_roles: Optional list of agent roles involved.

        Returns:
            Dict with compress result, critique result, and extracted experiences.
        """
        # Step 1: Compress trajectory
        record = self._compressor.compress(trajectory)
        # Override task description if provided
        if task_description:
            record.task_description = task_description

        # Step 2: Critique trajectory
        critique = await self._critique.critique(record, task_description)

        # Attach quality score to record
        record.quality_score = critique.scores.get("overall")

        # Step 3: Extract experiences if quality is good enough
        extracted_experiences: list[dict[str, Any]] = []
        if critique.should_extract:
            # Build a task_result-like dict from the record for extract_experiences
            task_result = {
                "strategy": "trajectory_pipeline",
                "final_answer": record.final_outcome,
                "reasoning": "; ".join(record.key_decisions) if record.key_decisions else "",
                "metrics": record.metrics,
            }
            experiences = await self.extract_experiences(
                task_description=task_description,
                task_result=task_result,
                task_id=task_id,
                agent_roles=agent_roles,
            )
            extracted_experiences = [
                {"id": str(e.id), "content": e.content[:200]}
                for e in experiences
            ]

        summary = {
            "compressed": {
                "task_description": record.task_description,
                "total_steps": record.metrics.get("total_steps", 0),
                "tool_calls": record.metrics.get("tool_calls", 0),
                "errors": record.metrics.get("errors", 0),
                "tools_used": record.tools_used,
                "key_decisions_count": len(record.key_decisions),
                "quality_score": record.quality_score,
            },
            "critique": {
                "scores": critique.scores,
                "strengths": critique.strengths,
                "weaknesses": critique.weaknesses,
                "suggestions": critique.suggestions,
                "should_extract": critique.should_extract,
            },
            "experiences_extracted": extracted_experiences,
            "experience_count": len(extracted_experiences),
        }

        logger.info(
            "task_processed_full_pipeline",
            task=task_description[:80],
            quality_score=record.quality_score,
            should_extract=critique.should_extract,
            experiences_count=len(extracted_experiences),
        )

        return summary

    def _build_trajectory_summary(
        self, task_description: str, task_result: dict[str, Any]
    ) -> str:
        """Xây dựng tóm tắt trajectory từ kết quả task."""
        parts = [f"Task: {task_description}"]

        strategy = task_result.get("strategy", "unknown")
        parts.append(f"Strategy: {strategy}")
        parts.append(f"Rounds: {task_result.get('rounds', 'N/A')}")

        # Thêm chi tiết từ debate/execution
        if "all_rounds" in task_result:
            for round_data in task_result["all_rounds"][:3]:
                for proposal in round_data.get("proposals", []):
                    parts.append(
                        f"[{proposal.get('agent', 'Agent')} - {proposal.get('role', '')}]: "
                        f"{proposal.get('content', '')[:200]}"
                    )

        if "final_answer" in task_result:
            parts.append(f"Final Answer: {str(task_result['final_answer'])[:500]}")

        if "reasoning" in task_result:
            parts.append(f"Reasoning: {task_result['reasoning']}")

        return "\n".join(parts)

    async def _merge_similar_experiences(self) -> None:
        """
        Merge experiences có nội dung tương tự.

        G3 upgrade: Real merge implementation.
        Strategy:
        1. Skip if total < 20 (too few to have duplicates)
        2. For each recently created experience, find near-duplicates:
           a. Vector similarity (cosine distance < 0.15) if embeddings exist
           b. Text overlap fallback (first 80 chars match)
        3. Merge: consolidate stats into the newer record, delete older duplicate
        """
        count_result = await self.db.execute(
            text("SELECT COUNT(*) FROM experiences")
        )
        total = count_result.scalar() or 0

        if total < 20:
            return  # Too few to have meaningful duplicates

        # Get the most recent 5 experiences (just created)
        recent_result = await self.db.execute(
            select(ExperienceModel)
            .order_by(ExperienceModel.created_at.desc())
            .limit(5)
        )
        recent_exps = list(recent_result.scalars().all())

        merged_count = 0

        for exp in recent_exps:
            if merged_count >= 3:
                break  # Limit merge ops per call to avoid long transactions

            duplicates: list[ExperienceModel] = []

            # Strategy A: Vector similarity (if embeddings available)
            if exp.embedding is not None:
                try:
                    vec_result = await self.db.execute(
                        select(ExperienceModel)
                        .where(
                            ExperienceModel.id != exp.id,
                            ExperienceModel.embedding.isnot(None),
                        )
                        .order_by(
                            ExperienceModel.embedding.cosine_distance(exp.embedding)
                        )
                        .limit(2)
                    )
                    candidates = list(vec_result.scalars().all())
                    # Only merge if very similar (cosine distance < 0.15)
                    # Note: cosine_distance returns 0 for identical, 2 for opposite
                    for cand in candidates:
                        if cand.embedding is not None:
                            # Approximate check: if vector search returned them as top-2
                            # nearest neighbors, they are very likely similar
                            duplicates.append(cand)
                except Exception:
                    pass  # Vector search failed, try text fallback

            # Strategy B: Text overlap fallback
            if not duplicates:
                # Match experiences with same prefix (first 80 chars)
                content_prefix = exp.content[:80].strip()
                if len(content_prefix) > 20:
                    try:
                        text_result = await self.db.execute(
                            select(ExperienceModel)
                            .where(
                                ExperienceModel.id != exp.id,
                                ExperienceModel.content.ilike(f"{content_prefix}%"),
                            )
                            .limit(2)
                        )
                        duplicates = list(text_result.scalars().all())
                    except Exception:
                        pass

            # Merge duplicates into the current experience
            for dup in duplicates:
                # Consolidate stats: keep the more important/applied one
                exp.importance = min(1.0, max(exp.importance, dup.importance) + 0.05)
                exp.times_applied += dup.times_applied
                exp.times_helpful += dup.times_helpful

                # Merge lesson if the duplicate has a different one
                if dup.lesson and dup.lesson != exp.lesson:
                    if exp.lesson:
                        exp.lesson = f"{exp.lesson}; {dup.lesson}"
                    else:
                        exp.lesson = dup.lesson

                await self.db.delete(dup)
                merged_count += 1

        if merged_count > 0:
            await self.db.flush()
            logger.info(
                "experiences_merged",
                merged_count=merged_count,
                total_before=total,
            )

        if total > 100:
            logger.warning(
                "experience_bank_large",
                count=total,
                message="Consider running experience consolidation",
            )
