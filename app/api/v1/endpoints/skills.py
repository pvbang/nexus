"""
Nexus - Skills Hub API Endpoints.

CRUD operations for the Skills Hub marketplace:
discover, install, uninstall, search, import/export skills.
"""

from typing import Any, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.skills.hub import get_skills_marketplace
from app.skills.manifest import ALLOWED_CATEGORIES, SkillManifestError

logger = structlog.get_logger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Request/Response schemas
# ------------------------------------------------------------------


class SkillInstallRequest(BaseModel):
    """Request body for installing a new skill."""

    skill_id: str = Field(..., description="Unique skill slug (lowercase, hyphens)")
    name: str = Field(..., description="Display name")
    description: str = Field("", description="Skill description")
    version: str = Field("1.0.0", description="Semver version")
    author: str = Field("system", description="Author name")
    category: str = Field("utility", description="Skill category")
    tags: list[str] = Field(default_factory=list, description="Search tags")
    system_prompt: str = Field("", description="Skill system prompt / instructions")
    tools_required: list[str] = Field(default_factory=list, description="Required tool names")
    model_preference: Optional[str] = Field(None, description="Preferred model")
    temperature: Optional[float] = Field(None, description="Temperature override")
    examples: list[dict[str, Any]] = Field(default_factory=list, description="Example I/O pairs")
    source: str = Field("hub", description="Skill source (local/hub/generated)")


class SkillSearchRequest(BaseModel):
    """Request body for searching skills."""

    query: str = Field(..., description="Search query")
    category: Optional[str] = Field(None, description="Category filter")


class SkillImportRequest(BaseModel):
    """Request body for importing a skill from SKILL.md text."""

    content: str = Field(..., description="Raw SKILL.md content with YAML frontmatter")


class SkillResponse(BaseModel):
    """Standard skill response."""

    skill_id: str
    name: str
    description: str
    version: str
    author: str
    category: str
    tags: list[str]
    system_prompt: str
    tools_required: list[str]
    model_preference: Optional[str]
    temperature: Optional[float]
    examples: list[dict[str, Any]]
    enabled: bool
    installed_at: Optional[str]
    source: str


class MessageResponse(BaseModel):
    """Simple message response."""

    message: str
    success: bool = True


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.get("/", response_model=list[SkillResponse])
async def list_skills(
    category: Optional[str] = Query(None, description="Filter by category"),
) -> list[dict[str, Any]]:
    """List all skills, with optional category filter."""
    hub = get_skills_marketplace()
    skills = hub.discover()

    if category:
        if category not in ALLOWED_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category: '{category}'. Must be one of: {ALLOWED_CATEGORIES}",
            )
        skills = [s for s in skills if s.category == category]

    return [s.to_dict() for s in skills]


@router.get("/categories")
async def list_categories() -> list[dict[str, Any]]:
    """List all skill categories with counts."""
    hub = get_skills_marketplace()
    return hub.list_categories()


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    """Get hub statistics."""
    hub = get_skills_marketplace()
    return hub.get_stats()


@router.post("/search", response_model=list[SkillResponse])
async def search_skills(body: SkillSearchRequest) -> list[dict[str, Any]]:
    """Search skills by name/description/tags."""
    hub = get_skills_marketplace()
    results = hub.search(query=body.query, category=body.category)
    return [s.to_dict() for s in results]


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(skill_id: str) -> dict[str, Any]:
    """Get a single skill by ID."""
    hub = get_skills_marketplace()
    manifest = hub.get(skill_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    return manifest.to_dict()


@router.post("/", response_model=SkillResponse, status_code=201)
async def install_skill(body: SkillInstallRequest) -> dict[str, Any]:
    """Install a new skill from manifest data."""
    from app.skills.manifest import SkillManifest

    try:
        manifest = SkillManifest(
            skill_id=body.skill_id,
            name=body.name,
            description=body.description,
            version=body.version,
            author=body.author,
            category=body.category,
            tags=body.tags,
            system_prompt=body.system_prompt,
            tools_required=body.tools_required,
            model_preference=body.model_preference,
            temperature=body.temperature,
            examples=body.examples,
            source=body.source,
        )
    except SkillManifestError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    hub = get_skills_marketplace()

    # Check if skill already exists
    existing = hub.get(body.skill_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Skill already exists: {body.skill_id}. Uninstall first to reinstall.",
        )

    success = hub.install(manifest)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to install skill")

    return manifest.to_dict()


@router.delete("/{skill_id}", response_model=MessageResponse)
async def uninstall_skill(skill_id: str) -> dict[str, Any]:
    """Uninstall a skill by ID."""
    hub = get_skills_marketplace()
    manifest = hub.get(skill_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")

    success = hub.uninstall(skill_id)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to uninstall skill: {skill_id}")

    return {"message": f"Skill '{skill_id}' uninstalled successfully", "success": True}


@router.post("/{skill_id}/enable", response_model=MessageResponse)
async def enable_skill(skill_id: str) -> dict[str, Any]:
    """Enable a skill."""
    hub = get_skills_marketplace()
    success = hub.enable(skill_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    return {"message": f"Skill '{skill_id}' enabled", "success": True}


@router.post("/{skill_id}/disable", response_model=MessageResponse)
async def disable_skill(skill_id: str) -> dict[str, Any]:
    """Disable a skill."""
    hub = get_skills_marketplace()
    success = hub.disable(skill_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    return {"message": f"Skill '{skill_id}' disabled", "success": True}


@router.post("/import", response_model=SkillResponse, status_code=201)
async def import_skill(body: SkillImportRequest) -> dict[str, Any]:
    """Import a skill from raw SKILL.md text content."""
    hub = get_skills_marketplace()
    try:
        manifest = await hub.import_from_text(body.content)
    except (SkillManifestError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.error("skill_import_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}")

    return manifest.to_dict()


@router.get("/{skill_id}/export")
async def export_skill(skill_id: str) -> dict[str, Any]:
    """Export a skill as SKILL.md text content."""
    hub = get_skills_marketplace()
    content = hub.export(skill_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Skill not found: {skill_id}")
    return {"skill_id": skill_id, "content": content}
