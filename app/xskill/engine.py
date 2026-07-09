"""
MekongAI Nexus - XSkill Continual Learning Engine.

Tầng cao nhất: điều phối Experience Manager + Skill Builder.
Tích hợp trực tiếp vào Core Orchestrator.
"""

import re
from typing import Any, Optional
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_client import LLMClient, get_core_llm
from app.xskill.experience_manager import ExperienceManager
from app.xskill.skill_builder import SkillBuilder

logger = structlog.get_logger(__name__)

# ── Trivial Task Gate: patterns that should NEVER create skills ──
_TRIVIAL_PATTERNS = [
    "xin chào", "hello", "hi", "hey", "chào", "thanks", "cảm ơn",
    "bye", "tạm biệt", "ok", "okay", "good", "tốt", "được",
    "yes", "no", "vâng", "không", "ừ", "uhm",
]
_TRIVIAL_REGEX = re.compile(
    r"^(" + "|".join(re.escape(p) for p in _TRIVIAL_PATTERNS) + r")[\s!?.,:;]*$",
    re.IGNORECASE,
)

# Minimum thresholds for skill accumulation
_MIN_TASK_DESC_LENGTH = 20  # chars
_MIN_RESPONSE_LENGTH = 300  # chars
_MIN_TOOL_CALLS = 0  # 0 = allow tasks without tool calls IF response is substantial


class ContinualLearningEngine:
    """
    XSkill Continual Learning Engine.

    Cung cấp 2 operations chính:
    1. retrieve_knowledge() - gọi TRƯỚC khi execute task
    2. accumulate_knowledge() - gọi SAU khi task hoàn thành
    """

    def __init__(self, db: AsyncSession, llm: Optional[LLMClient] = None):
        self.db = db
        self.llm = llm or get_core_llm()
        self.experience_mgr = ExperienceManager(db, self.llm)
        self.skill_builder = SkillBuilder(db, self.llm)

    async def retrieve_knowledge(
        self,
        task_description: str,
        max_skills: int = 3,
        max_experiences: int = 5,
    ) -> dict[str, Any]:
        """
        Phase 2 (Inference): Retrieve knowledge TRƯỚC khi execute task.

        Trả về skills và experiences phù hợp để inject vào agent prompts.
        """
        skills = await self.skill_builder.retrieve_relevant_skills(
            task_description, limit=max_skills
        )
        experiences = await self.experience_mgr.retrieve_relevant(
            task_description, limit=max_experiences
        )

        # Build context string cho injection vào system prompts
        context_parts: list[str] = []

        if skills:
            context_parts.append("## Relevant Skills (proven workflows)")
            for i, skill in enumerate(skills, 1):
                context_parts.append(
                    f"\n### Skill {i}: {skill['title']} "
                    f"(effectiveness: {skill['effectiveness']:.0%})\n"
                    f"{skill['content']}"
                )

        if experiences:
            context_parts.append("\n## Relevant Experiences (tactical lessons)")
            for i, exp in enumerate(experiences, 1):
                context_parts.append(
                    f"\n{i}. [{exp['type']}] {exp['content']}"
                    + (f"\n   Lesson: {exp['lesson']}" if exp.get("lesson") else "")
                )

        knowledge_context = "\n".join(context_parts) if context_parts else ""

        logger.info(
            "knowledge_retrieved",
            skills_count=len(skills),
            experiences_count=len(experiences),
            context_length=len(knowledge_context),
        )

        return {
            "skills": skills,
            "experiences": experiences,
            "context": knowledge_context,
            "has_prior_knowledge": bool(skills or experiences),
        }

    def _should_accumulate(
        self,
        task_description: str,
        task_result: dict[str, Any],
        trajectory: Optional[list[dict[str, Any]]] = None,
    ) -> bool:
        """
        Trivial Task Gate: chỉ tích lũy knowledge khi task có giá trị học hỏi.

        Prevents skill/experience creation for:
        - Greetings ("Xin chào", "Hello", etc.)
        - Very short tasks (< 30 chars)
        - Trivial responses (< 300 chars output)
        - Tasks without meaningful execution (no tool calls AND short response)
        """
        # Rule 1: Task description quá ngắn = trivial
        desc = task_description.strip()
        if len(desc) < _MIN_TASK_DESC_LENGTH:
            logger.debug("xskill_gate_skip_short_desc", desc_len=len(desc))
            return False

        # Rule 2: Matches trivial greeting/chitchat patterns
        if _TRIVIAL_REGEX.match(desc):
            logger.debug("xskill_gate_skip_trivial_pattern", desc=desc[:50])
            return False

        # Rule 3: Response too short = trivial answer
        content = (
            task_result.get("content", "")
            or task_result.get("answer", "")
            or task_result.get("final_answer", "")
        )
        tool_calls_total = task_result.get("tool_calls_total", 0)
        tools_used = task_result.get("tools_used", [])
        has_tool_usage = tool_calls_total > 0 or len(tools_used) > 0

        if len(content) < _MIN_RESPONSE_LENGTH and not has_tool_usage:
            logger.debug(
                "xskill_gate_skip_short_response",
                content_len=len(content),
                tool_calls=tool_calls_total,
            )
            return False

        # Rule 4: Trajectory too short (< 3 meaningful steps) AND no tool calls
        if trajectory and len(trajectory) < 3 and not has_tool_usage:
            logger.debug("xskill_gate_skip_shallow_trajectory", steps=len(trajectory))
            return False

        return True

    async def accumulate_knowledge(
        self,
        task_description: str,
        task_result: dict[str, Any],
        task_id: Optional[UUID] = None,
        task_status: str = "completed",
        agents_used: Optional[list[dict[str, str]]] = None,
        trajectory: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """
        Phase 1 (Accumulation): Đúc kết knowledge SAU khi task hoàn thành.

        Chạy background: tạo Skills + Experiences từ trajectory.

        If trajectory is provided (≥3 entries), uses the full pipeline:
        compress → critique → extract (via ExperienceManager.process_completed_task).
        Otherwise falls back to the simpler extract_experiences() path.

        GATE: Trivial tasks (greetings, short Q&A) are skipped entirely.
        """
        accumulated: dict[str, Any] = {
            "skill": None,
            "experiences": [],
            "skill_id": None,
            "experience_count": 0,
        }

        # ── Trivial Task Gate ──
        if not self._should_accumulate(task_description, task_result, trajectory):
            logger.info(
                "xskill_accumulate_skipped_trivial",
                task_description=task_description[:80],
                task_id=str(task_id) if task_id else None,
            )
            return accumulated

        # Chỉ build skill từ task thành công
        if task_status == "completed":
            skill = await self.skill_builder.build_skill(
                task_description=task_description,
                task_result=task_result,
                task_id=task_id,
                agents_used=agents_used,
            )
            if skill:
                accumulated["skill"] = {
                    "id": str(skill.id),
                    "title": skill.title,
                    "task_type": skill.task_type,
                }
                accumulated["skill_id"] = str(skill.id)

        # --- Experience extraction ---
        # If trajectory is provided with enough entries, use the full pipeline:
        # compress → critique → extract (Gap 3 fix)
        if trajectory is not None and len(trajectory) >= 3:
            try:
                pipeline_result = await self.experience_mgr.process_completed_task(
                    trajectory=trajectory,
                    task_description=task_description,
                    task_id=task_id,
                    agent_roles=[a.get("role", "") for a in (agents_used or [])],
                )
                accumulated["experience_count"] = pipeline_result.get("experience_count", 0)
                accumulated["experiences"] = pipeline_result.get("experiences_extracted", [])
                accumulated["pipeline"] = "full"  # mark which path was used

                logger.info(
                    "knowledge_accumulated_full_pipeline",
                    task_id=str(task_id) if task_id else None,
                    skill_built=accumulated["skill"] is not None,
                    experiences_extracted=accumulated["experience_count"],
                    quality_score=pipeline_result.get("compressed", {}).get("quality_score"),
                )
                return accumulated
            except Exception as e:
                logger.warning(
                    "full_pipeline_failed_fallback",
                    error=str(e),
                    task_id=str(task_id) if task_id else None,
                )
                # Fall through to simple extraction below

        # Fallback: simple extract_experiences (backward compatible)
        experiences = await self.experience_mgr.extract_experiences(
            task_description=task_description,
            task_result=task_result,
            task_id=task_id,
            agent_roles=[a.get("role", "") for a in (agents_used or [])],
        )
        accumulated["experience_count"] = len(experiences)
        accumulated["experiences"] = [
            {"id": str(e.id), "content": e.content[:100]}
            for e in experiences
        ]
        accumulated["pipeline"] = "simple"

        logger.info(
            "knowledge_accumulated",
            task_id=str(task_id) if task_id else None,
            skill_built=accumulated["skill"] is not None,
            experiences_extracted=len(experiences),
        )

        return accumulated

    async def get_stats(self) -> dict[str, Any]:
        """Thống kê XSkill knowledge base."""
        skill_stats = await self.skill_builder.get_stats()
        experiences = await self.experience_mgr.list_all(limit=1)

        from sqlalchemy import text
        exp_count = await self.db.execute(text("SELECT COUNT(*) FROM experiences"))

        return {
            "skills": skill_stats,
            "experiences": {
                "total": exp_count.scalar() or 0,
            },
        }
