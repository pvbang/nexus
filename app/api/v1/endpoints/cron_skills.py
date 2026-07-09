"""
MekongAI Nexus - Cron & Skills API Endpoints.

Provides REST endpoints for:
- Cron job management (CRUD + trigger)
- Skills Hub (list, invoke, scan)
"""

from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.cron import CronJob, get_scheduler
from app.skills import get_skills_hub

router = APIRouter()


# ── Cron Job Schemas ──────────────────────────────────────────────────

class CronJobCreate(BaseModel):
    name: str = Field(..., description="Job name")
    prompt: str = Field(..., description="Prompt for the agent")
    schedule: str = Field(..., description="Cron schedule expression")
    deliver: str = Field("local", description="Delivery channel: websocket, telegram, email, or local")
    deliver_target: str = Field("", description="Delivery target: user_id, chat_id, or email")
    skills: list[str] = Field(default_factory=list, description="Skills to load")
    model: Optional[str] = Field(None, description="Model override")
    max_iterations: int = Field(50, description="Max iterations")


class CronJobUpdate(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    schedule: Optional[str] = None
    enabled: Optional[bool] = None
    deliver: Optional[str] = None
    deliver_target: Optional[str] = None
    skills: Optional[list[str]] = None


def _job_to_dict(job: CronJob) -> dict[str, Any]:
    """Convert CronJob dataclass to dict for JSON response."""
    return asdict(job)


# ── Cron Endpoints ────────────────────────────────────────────────────

@router.get("/cron/jobs", tags=["cron"])
async def list_cron_jobs():
    """List all cron jobs."""
    scheduler = get_scheduler()
    return {"jobs": [_job_to_dict(j) for j in scheduler.list_jobs()]}


@router.post("/cron/jobs", tags=["cron"])
async def create_cron_job(data: CronJobCreate):
    """Create a new cron job.

    Maps CronJobCreate schema fields to CronJob dataclass fields:
    - prompt → task_message
    - schedule → cron_expression
    - deliver → delivery_channel
    - deliver_target → delivery_target
    - skills, model, max_iterations → stored in metadata
    """
    scheduler = get_scheduler()
    # Map API schema fields → CronJob dataclass fields via create_job()
    job = scheduler.create_job(
        name=data.name,
        cron_expression=data.schedule,
        task_message=data.prompt,
        delivery_channel=data.deliver if data.deliver != "local" else "",
        delivery_target=data.deliver_target,
        metadata={
            "skills": data.skills,
            "model": data.model,
            "max_iterations": data.max_iterations,
        },
    )
    return {"job": _job_to_dict(job)}


@router.get("/cron/jobs/{job_id}", tags=["cron"])
async def get_cron_job(job_id: str):
    """Get a specific cron job."""
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job": _job_to_dict(job)}


@router.put("/cron/jobs/{job_id}", tags=["cron"])
async def update_cron_job(job_id: str, data: CronJobUpdate):
    """Update a cron job."""
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Map API schema fields → CronJob dataclass fields
    if data.name is not None:
        job.name = data.name
    if data.prompt is not None:
        job.task_message = data.prompt
    if data.schedule is not None:
        job.cron_expression = data.schedule
    if data.enabled is not None:
        scheduler.toggle_job(job_id, data.enabled)
    if data.deliver is not None:
        job.delivery_channel = data.deliver if data.deliver != "local" else ""
    if data.deliver_target is not None:
        job.delivery_target = data.deliver_target
    if data.skills is not None:
        job.metadata["skills"] = data.skills

    return {"job": _job_to_dict(job)}


@router.delete("/cron/jobs/{job_id}", tags=["cron"])
async def delete_cron_job(job_id: str):
    """Delete a cron job."""
    scheduler = get_scheduler()
    if scheduler.delete_job(job_id):
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Job not found")


@router.post("/cron/tick", tags=["cron"])
async def trigger_cron_tick():
    """Manually trigger cron scheduler to check and run due jobs."""
    scheduler = get_scheduler()
    # Start scheduler if not already running (triggers APScheduler check)
    started = await scheduler.start()
    return {"scheduler_active": started, "jobs_count": len(scheduler.list_jobs())}


# ── Skills Schemas ────────────────────────────────────────────────────

class SkillInvokeRequest(BaseModel):
    skill_name: str = Field(..., description="Skill name or /command")
    instruction: str = Field("", description="User instruction alongside skill")


# ── Skill Runner Endpoints (file-based SKILL.md skills) ───────────────
# NOTE: These use /skill-runner prefix to avoid route collision with
# /skills (SkillsMarketplace CRUD in skills.py). This module exposes
# the lightweight SkillsHub (scan/invoke file-based skills).

@router.get("/skill-runner", tags=["skill-runner"])
async def list_runner_skills():
    """List all discovered file-based skills."""
    hub = get_skills_hub()
    return {"skills": hub.list_skills()}


@router.get("/skill-runner/commands", tags=["skill-runner"])
async def list_skill_commands():
    """List all skill slash commands."""
    hub = get_skills_hub()
    return {"commands": hub.list_commands()}


@router.post("/skill-runner/scan", tags=["skill-runner"])
async def scan_skills():
    """Re-scan skills directories."""
    hub = get_skills_hub()
    skills = hub.scan()
    return {"scanned": len(skills), "skills": hub.list_skills()}


@router.post("/skill-runner/invoke", tags=["skill-runner"])
async def invoke_skill(data: SkillInvokeRequest):
    """Invoke a skill and return its formatted message."""
    hub = get_skills_hub()
    result = hub.invoke_skill(data.skill_name, data.instruction)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {data.skill_name}")
    return {"message": result}


@router.get("/skill-runner/{name}", tags=["skill-runner"])
async def get_runner_skill(name: str):
    """Get a specific file-based skill by name."""
    hub = get_skills_hub()
    skill = hub.get_skill(name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill not found: {name}")
    return {"skill": skill.to_dict(), "content": skill.content}
