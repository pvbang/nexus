"""
MekongAI Nexus - Agent Endpoints.

Includes:
- CRUD operations for agents
- Agent interview/introspection endpoints
- Graph updater stats
"""

from typing import Any, Optional
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import AgentCreate, AgentResponse, AgentUpdate
from app.core.dependencies import get_db
from app.memory.graph_updater import get_graph_updater
from app.models.database import AgentModel
from app.services.agent_spawner import AgentSpawner

logger = structlog.get_logger(__name__)
router = APIRouter()


# ============================================================
# Interview Request Schemas
# ============================================================


class InterviewRequest(BaseModel):
    """Request body for agent interview."""

    question: str = Field(..., min_length=1, max_length=5000)


class BatchInterviewRequest(BaseModel):
    """Request body for batch agent interview."""

    agent_ids: list[UUID] = Field(..., min_length=1)
    question: str = Field(..., min_length=1, max_length=5000)


class SwarmInterviewRequest(BaseModel):
    """Request body for swarm interview."""

    swarm_id: UUID
    question: str = Field(..., min_length=1, max_length=5000)


# ============================================================
# Graph Stats (must come before /{agent_id} to avoid path conflict)
# ============================================================


@router.get("/graph-stats")
async def get_graph_stats() -> dict[str, Any]:
    """Get knowledge graph updater statistics."""
    return get_graph_updater().get_stats()


@router.post("/seed-defaults")
async def seed_default_agents(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Pre-populate DB with one agent per template role.

    Idempotent: skips roles that already have an agent.
    Safe to call multiple times — only creates missing ones.
    """
    spawner = AgentSpawner(db)
    created = await spawner.seed_default_agents()
    return {
        "created": len(created),
        "agents": [
            {
                "id": str(a.id),
                "name": a.name,
                "role": a.role,
                "avatar": a.avatar,
            }
            for a in created
        ],
        "message": f"Seeded {len(created)} new default agents" if created else "All default agents already exist",
    }


@router.post("/refresh-prompts")
async def refresh_agent_prompts(
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Rebuild system prompts for all existing default agents.

    Use after updating AGENT_TEMPLATES or prompt_sections.
    Updates: system_prompt, tools, description, personality, avatar,
    expertise, thinking_style, background for each existing agent.
    """
    from sqlalchemy import select

    from app.core.prompt_builder import get_prompt_builder
    from app.services.agent_spawner import AGENT_TEMPLATES, AGENT_BASE_TOOLS, build_agent_tool_list

    prompt_builder = get_prompt_builder()
    updated = []

    for role, template in AGENT_TEMPLATES.items():
        result = await db.execute(
            select(AgentModel)
            .where(AgentModel.role == role)
            .limit(1)
        )
        agent = result.scalar_one_or_none()
        if not agent:
            continue

        # Rebuild merged tool list (same logic as spawn_from_template + enrichment)
        tool_names = build_agent_tool_list(template)

        # Rebuild system prompt
        system_prompt = prompt_builder.build_system_prompt(
            agent_type=role,
            tool_names=tool_names,
            include_self_solving=(role != "judge"),
        )

        # Update agent fields
        agent.system_prompt = system_prompt
        agent.tools = tool_names
        agent.description = template.get("description") or agent.description
        agent.personality = template.get("personality") or agent.personality
        agent.avatar = template.get("avatar") or agent.avatar
        agent.expertise = template.get("expertise") or agent.expertise
        agent.thinking_style = template.get("thinking_style") or agent.thinking_style
        agent.background = template.get("background") or agent.background
        agent.skills = template.get("skills") or agent.skills

        updated.append({"id": str(agent.id), "role": role, "name": agent.name})

    if updated:
        await db.commit()

    return {
        "refreshed": len(updated),
        "agents": updated,
        "message": f"Rebuilt prompts for {len(updated)} agents" if updated else "No agents found to refresh",
    }


# ============================================================
# Interview Endpoints (must come before /{agent_id} to avoid path conflict)
# ============================================================


@router.post("/interview/batch")
async def interview_batch(
    request: BatchInterviewRequest,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """
    Interview multiple agents with the same question in parallel.

    Uses asyncio.gather() for concurrent interviews.
    """
    spawner = AgentSpawner(db)
    try:
        results = await spawner.interview_batch(
            agent_ids=request.agent_ids,
            question=request.question,
        )
        return results
    except Exception as e:
        logger.error("batch_interview_api_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Batch interview failed: {e}")


@router.post("/interview/swarm")
async def interview_swarm(
    request: SwarmInterviewRequest,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Interview all agents in a swarm with the same question."""
    spawner = AgentSpawner(db)
    try:
        results = await spawner.interview_swarm(
            swarm_id=request.swarm_id,
            question=request.question,
        )
        return results
    except Exception as e:
        logger.error("swarm_interview_api_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Swarm interview failed: {e}")


# ============================================================
# Standard Agent CRUD
# ============================================================


@router.get("/")
async def list_agents(
    status: str | None = None,
    role: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Liệt kê tất cả agents."""
    stmt = select(AgentModel).order_by(AgentModel.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(AgentModel.status == status)
    if role:
        stmt = stmt.where(AgentModel.role == role)
    result = await db.execute(stmt)
    agents = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "role": a.role,
            "description": a.description,
            "personality": a.personality,
            "status": a.status,
            "llm_model": a.llm_model,
            "swarm_id": str(a.swarm_id) if a.swarm_id else None,
            "skills": a.skills,
            "tools": a.tools,
            "avatar": a.avatar,
            "expertise": a.expertise,
            "thinking_style": a.thinking_style,
            "background": a.background,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in agents
    ]


@router.get("/{agent_id}")
async def get_agent(
    agent_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Lấy chi tiết agent."""
    result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "id": str(agent.id),
        "name": agent.name,
        "role": agent.role,
        "description": agent.description,
        "personality": agent.personality,
        "system_prompt": agent.system_prompt,
        "status": agent.status,
        "skills": agent.skills,
        "tools": agent.tools,
        "llm_model": agent.llm_model,
        "avatar": agent.avatar,
        "expertise": agent.expertise,
        "thinking_style": agent.thinking_style,
        "background": agent.background,
        "swarm_id": str(agent.swarm_id) if agent.swarm_id else None,
        "parent_agent_id": str(agent.parent_agent_id) if agent.parent_agent_id else None,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


@router.post("/{agent_id}/interview")
async def interview_agent(
    agent_id: UUID,
    request: InterviewRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Interview a specific agent — ask it about its state, reasoning, or opinions.

    Creates a temporary LLM conversation with agent's system prompt + memory,
    sends the question, returns the response.
    """
    spawner = AgentSpawner(db)
    try:
        result = await spawner.interview_agent(
            agent_id=agent_id,
            question=request.question,
        )
        return result
    except Exception as e:
        logger.error("interview_agent_api_failed", agent_id=str(agent_id), error=str(e))
        raise HTTPException(status_code=500, detail=f"Interview failed: {e}")


@router.post("/spawn")
async def spawn_agent(
    request: AgentCreate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Spawn agent mới thủ công."""
    spawner = AgentSpawner(db)
    agent = await spawner.spawn_from_template(
        role=request.role,
        name=request.name,
        overrides={
            "description": request.description,
            "personality": request.personality,
            "system_prompt": request.system_prompt,
            "skills": request.skills,
            "tools": request.tools,
            "llm_model": request.llm_model,
        },
    )
    return {
        "id": str(agent.id),
        "name": agent.name,
        "role": agent.role,
        "description": agent.description,
        "personality": agent.personality,
        "status": agent.status,
        "llm_model": agent.llm_model,
        "skills": agent.skills,
        "tools": agent.tools,
        "avatar": agent.avatar,
        "expertise": agent.expertise,
        "thinking_style": agent.thinking_style,
        "background": agent.background,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


@router.patch("/{agent_id}")
async def update_agent(
    agent_id: UUID,
    request: AgentUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Cập nhật thông tin agent."""
    result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = request.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agent, field, value)

    await db.flush()
    await db.commit()
    logger.info("agent_updated", agent_id=str(agent_id), fields=list(update_data.keys()))

    return {
        "id": str(agent.id),
        "name": agent.name,
        "role": agent.role,
        "description": agent.description,
        "personality": agent.personality,
        "system_prompt": agent.system_prompt,
        "status": agent.status,
        "skills": agent.skills,
        "tools": agent.tools,
        "llm_model": agent.llm_model,
        "avatar": agent.avatar,
        "expertise": agent.expertise,
        "thinking_style": agent.thinking_style,
        "background": agent.background,
        "swarm_id": str(agent.swarm_id) if agent.swarm_id else None,
        "parent_agent_id": str(agent.parent_agent_id) if agent.parent_agent_id else None,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }


# ============================================================
# Agent Memory & Task History (Phase 8)
# ============================================================


@router.get("/{agent_id}/memories")
async def get_agent_memories(
    agent_id: UUID,
    limit: int = 10,
    memory_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Get agent's persistent memories including task learnings."""
    from app.models.database import AgentMemoryModel

    stmt = (
        select(AgentMemoryModel)
        .where(AgentMemoryModel.agent_id == agent_id)
        .order_by(AgentMemoryModel.created_at.desc())
        .limit(limit)
    )
    if memory_type:
        stmt = stmt.where(AgentMemoryModel.memory_type == memory_type)

    result = await db.execute(stmt)
    memories = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "content": m.content,
            "memory_type": m.memory_type,
            "importance": m.importance,
            "metadata": m.metadata_,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in memories
    ]


@router.get("/{agent_id}/task-history")
async def get_agent_task_history(
    agent_id: UUID,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get tasks this agent has participated in (via swarm membership)."""
    from app.models.database import TaskModel

    # Find swarms this agent was part of, then get their tasks
    result = await db.execute(
        select(AgentModel).where(AgentModel.id == agent_id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get memory file stats
    try:
        from app.memory.agent_memory_files import AgentMemoryFiles
        memory_files = AgentMemoryFiles(str(agent_id), agent.name)
        recent_history = memory_files.load_recent_history(limit=limit)
        stats = memory_files.get_stats()
    except Exception:
        recent_history = []
        stats = {}

    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "memory_stats": stats,
        "recent_activity": recent_history,
    }
