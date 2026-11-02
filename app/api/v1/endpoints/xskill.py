"""
Nexus - XSkill Endpoints (Continual Learning).

Endpoints:
- GET  /stats           — Thống kê knowledge base
- GET  /skills          — Liệt kê skills
- GET  /experiences     — Liệt kê experiences
- POST /retrieve        — Retrieve knowledge cho task
- POST /trajectory/compress  — Compress raw trajectory (Phase 5b)
- POST /trajectory/critique  — Critique trajectory quality (Phase 5b)
- POST /trajectory/process   — Full pipeline: compress → critique → extract (Phase 5b)
"""

from typing import Any, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.xskill.engine import ContinualLearningEngine
from app.xskill.experience_critique import ExperienceCritique
from app.xskill.trajectory_compressor import TrajectoryCompressor

logger = structlog.get_logger(__name__)
router = APIRouter()


# --- Pydantic request/response schemas ---


class TrajectoryStep(BaseModel):
    """Single step in a raw trajectory."""

    role: str = Field(..., description="Step role: user, assistant, tool")
    content: str = Field("", description="Message content")
    tool_calls: Optional[list[dict[str, Any]]] = Field(
        None, description="Tool calls made in this step"
    )
    tool_results: Optional[list[dict[str, Any]]] = Field(
        None, description="Tool results received"
    )
    timestamp: Optional[str] = Field(
        None, description="ISO 8601 timestamp"
    )
    usage: Optional[dict[str, int]] = Field(
        None, description="Token usage (prompt_tokens, completion_tokens, total_tokens)"
    )


class CompressRequest(BaseModel):
    """Request body for trajectory compression."""

    trajectory: list[TrajectoryStep] = Field(
        ..., description="Raw trajectory steps"
    )


class CritiqueRequest(BaseModel):
    """Request body for trajectory critique."""

    trajectory: list[TrajectoryStep] = Field(
        ..., description="Raw trajectory steps"
    )
    task_description: Optional[str] = Field(
        None, description="Override task description (auto-detected from trajectory if omitted)"
    )


class ProcessRequest(BaseModel):
    """Request body for full pipeline: compress → critique → extract."""

    trajectory: list[TrajectoryStep] = Field(
        ..., description="Raw trajectory steps"
    )
    task_description: str = Field(
        ..., description="Task description for experience extraction"
    )
    task_id: Optional[str] = Field(
        None, description="Task UUID for linking experiences"
    )
    agent_roles: Optional[list[str]] = Field(
        None, description="Agent roles involved in the task"
    )


# --- Existing endpoints ---


@router.get("/stats")
async def xskill_stats(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Thống kê XSkill knowledge base."""
    engine = ContinualLearningEngine(db)
    return await engine.get_stats()


@router.get("/skills")
async def list_skills(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Liệt kê tất cả skills đã học."""
    engine = ContinualLearningEngine(db)
    return await engine.skill_builder.list_all(limit=limit)


@router.get("/experiences")
async def list_experiences(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Liệt kê tất cả experiences đã học."""
    engine = ContinualLearningEngine(db)
    return await engine.experience_mgr.list_all(limit=limit)


@router.get("/skills/{skill_id}")
async def get_skill_detail(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get full detail of a specific skill."""
    from app.models.database import SkillModel
    from sqlalchemy import select

    try:
        uid = UUID(skill_id)
    except ValueError:
        return {"error": "Invalid skill ID"}

    result = await db.execute(
        select(SkillModel).where(SkillModel.id == uid)
    )
    skill = result.scalar_one_or_none()
    if not skill:
        return {"error": "Skill not found"}

    return {
        "id": str(skill.id),
        "title": skill.title,
        "task_type": skill.task_type,
        "content": skill.content,
        "tools_used": skill.tools_used,
        "effectiveness_score": skill.effectiveness_score,
        "success_count": skill.success_count,
        "fail_count": skill.fail_count,
        "source_task_id": str(skill.source_task_id) if skill.source_task_id else None,
        "metadata": skill.metadata_,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


@router.get("/experiences/{experience_id}")
async def get_experience_detail(
    experience_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get full detail of a specific experience."""
    from app.models.database import ExperienceModel
    from sqlalchemy import select

    try:
        uid = UUID(experience_id)
    except ValueError:
        return {"error": "Invalid experience ID"}

    result = await db.execute(
        select(ExperienceModel).where(ExperienceModel.id == uid)
    )
    exp = result.scalar_one_or_none()
    if not exp:
        return {"error": "Experience not found"}

    return {
        "id": str(exp.id),
        "content": exp.content,
        "experience_type": exp.experience_type,
        "context": exp.context,
        "lesson": exp.lesson,
        "importance": exp.importance,
        "times_applied": exp.times_applied,
        "times_helpful": exp.times_helpful,
        "source_task_id": str(exp.source_task_id) if exp.source_task_id else None,
        "source_agent_id": str(exp.source_agent_id) if exp.source_agent_id else None,
        "metadata": exp.metadata_,
        "created_at": exp.created_at.isoformat() if exp.created_at else None,
        "updated_at": exp.updated_at.isoformat() if exp.updated_at else None,
    }


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Xóa một skill khỏi knowledge base."""
    from app.models.database import SkillModel
    from sqlalchemy import select

    try:
        uid = UUID(skill_id)
    except ValueError:
        return {"error": "Invalid skill ID"}

    result = await db.execute(
        select(SkillModel).where(SkillModel.id == uid)
    )
    skill = result.scalar_one_or_none()
    if not skill:
        return {"error": "Skill not found"}

    await db.delete(skill)
    await db.commit()
    return {"success": True, "deleted_id": skill_id}


@router.delete("/experiences/{experience_id}")
async def delete_experience(
    experience_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Xóa một experience khỏi knowledge base."""
    from app.models.database import ExperienceModel
    from sqlalchemy import select

    try:
        uid = UUID(experience_id)
    except ValueError:
        return {"error": "Invalid experience ID"}

    result = await db.execute(
        select(ExperienceModel).where(ExperienceModel.id == uid)
    )
    exp = result.scalar_one_or_none()
    if not exp:
        return {"error": "Experience not found"}

    await db.delete(exp)
    await db.commit()
    return {"success": True, "deleted_id": experience_id}


@router.post("/retrieve")
async def retrieve_knowledge(
    task_description: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Preview: xem knowledge nào sẽ được retrieve cho task."""
    engine = ContinualLearningEngine(db)
    return await engine.retrieve_knowledge(task_description)


# --- Phase 5b: Trajectory endpoints ---


@router.post("/trajectory/compress")
async def compress_trajectory(
    body: CompressRequest,
) -> dict[str, Any]:
    """
    Compress raw trajectory thành structured TrajectoryRecord.

    Không cần DB — chỉ xử lý data locally.
    Returns: compressed record metrics, key decisions, tools, markdown report.
    """
    compressor = TrajectoryCompressor()
    raw_steps = [step.model_dump() for step in body.trajectory]
    record = compressor.compress(raw_steps)

    return {
        "task_description": record.task_description,
        "metrics": record.metrics,
        "key_decisions": record.key_decisions,
        "tools_used": record.tools_used,
        "errors_encountered": record.errors_encountered,
        "final_outcome": record.final_outcome[:500] if record.final_outcome else "",
        "steps_count": len(record.steps),
        "compressed_at": record.compressed_at.isoformat(),
        "markdown": compressor.to_markdown(record),
        "training_format": compressor.to_training_format(record),
    }


@router.post("/trajectory/critique")
async def critique_trajectory(
    body: CritiqueRequest,
) -> dict[str, Any]:
    """
    Critique trajectory quality using LLM-as-judge.

    Compress → critique. Returns scores, strengths, weaknesses, suggestions.
    """
    compressor = TrajectoryCompressor()
    raw_steps = [step.model_dump() for step in body.trajectory]
    record = compressor.compress(raw_steps)

    if body.task_description:
        record.task_description = body.task_description

    critique_engine = ExperienceCritique()
    result = await critique_engine.critique(record, body.task_description)

    return {
        "task_description": result.task_description,
        "scores": result.scores,
        "strengths": result.strengths,
        "weaknesses": result.weaknesses,
        "suggestions": result.suggestions,
        "should_extract": result.should_extract,
        "critiqued_at": result.critiqued_at.isoformat(),
    }


@router.post("/trajectory/process")
async def process_trajectory(
    body: ProcessRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Full pipeline: compress → critique → extract experience.

    Nếu trajectory đạt quality threshold, tự động extract experiences vào DB.
    """
    engine = ContinualLearningEngine(db)

    raw_steps = [step.model_dump() for step in body.trajectory]

    # Parse task_id if provided
    task_uuid: Optional[UUID] = None
    if body.task_id:
        try:
            task_uuid = UUID(body.task_id)
        except ValueError:
            pass

    result = await engine.experience_mgr.process_completed_task(
        trajectory=raw_steps,
        task_description=body.task_description,
        task_id=task_uuid,
        agent_roles=body.agent_roles,
    )

    return result
