"""
Nexus - Project Builder API Endpoints.

REST API for project building pipeline.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/project-builder", tags=["project-builder"])


# --- Request/Response schemas ---

class ProjectBuildRequest(BaseModel):
    request: str = Field(..., description="Project description/requirements")
    user_id: str = Field(default="default", description="User ID")
    max_iterations: int = Field(default=10, description="Max iteration cycles")

class ProjectBuildResponse(BaseModel):
    success: bool
    project_name: str = ""
    server_url: str = ""
    quality_score: float = 0.0
    iterations: int = 0
    files_written: int = 0
    phases_completed: list[str] = []
    error: str = ""

class BlueprintRequest(BaseModel):
    request: str = Field(..., description="Project description")
    context: str = Field(default="", description="Additional context")

class TemplateListResponse(BaseModel):
    templates: list[str]
    count: int


# --- Endpoints ---

@router.post("/build", response_model=ProjectBuildResponse, summary="Build a full project")
async def build_project(req: ProjectBuildRequest) -> ProjectBuildResponse:
    """
    Build a full project from description.

    Runs the complete pipeline: Blueprint → Scaffold → Implement → Test → Visual QA → Iterate.
    """
    from app.project_builder.pipeline import ProjectBuilderPipeline

    pipeline = ProjectBuilderPipeline(max_iterations=req.max_iterations)
    result = await pipeline.execute(req.request, user_id=req.user_id)

    return ProjectBuildResponse(
        success=result.success,
        project_name=result.blueprint.name if result.blueprint else "",
        server_url=(result.server.public_url or result.server.external_url or result.server.url) if result.server else "",
        quality_score=result.final_quality,
        iterations=result.iterations,
        files_written=result.progress.files_written if result.progress else 0,
        phases_completed=result.progress.phases_completed if result.progress else [],
        error=result.error,
    )


@router.post("/blueprint", summary="Generate project blueprint only")
async def generate_blueprint(req: BlueprintRequest) -> dict[str, Any]:
    """Generate project blueprint without building."""
    from app.project_builder.blueprint import BlueprintGenerator

    gen = BlueprintGenerator()
    blueprint = await gen.generate(req.request, context=req.context)
    return {"success": True, "blueprint": blueprint.to_dict()}


@router.get("/templates", response_model=TemplateListResponse, summary="List available templates")
async def list_templates() -> TemplateListResponse:
    """List available project templates."""
    from app.project_builder.templates import ProjectTemplateRegistry

    registry = ProjectTemplateRegistry()
    templates = registry.list_templates()
    return TemplateListResponse(templates=templates, count=len(templates))


@router.get("/servers", summary="List running dev servers")
async def list_servers() -> dict[str, Any]:
    """List all running development servers."""
    from app.project_builder.dev_server import DevServerManager

    mgr = DevServerManager()
    servers = mgr.list_servers()
    return {
        "servers": [
            {"server_id": s.server_id, "url": s.url, "port": s.port, "tech": s.tech, "status": s.status}
            for s in servers
        ],
        "count": len(servers),
    }


@router.delete("/servers/{server_id}", summary="Stop a dev server")
async def stop_server(server_id: str) -> dict[str, Any]:
    """Stop a running development server."""
    from app.project_builder.dev_server import DevServerManager

    mgr = DevServerManager()
    success = await mgr.stop_server(server_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return {"success": True, "server_id": server_id}
