"""
Nexus - Tool Endpoints.
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.mcp.tool_registry import ToolRegistry

logger = structlog.get_logger(__name__)
router = APIRouter()


class ToolGenerateRequest(BaseModel):
    """Request body for tool generation."""

    description: str = Field(..., description="Description of the tool to generate")


@router.get("/")
async def list_tools(
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Liệt kê tất cả tools."""
    registry = ToolRegistry(db)
    return await registry.list_all_tools()


@router.post("/generate")
async def generate_tool(
    body: ToolGenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Generate tool mới bằng Coding Engine."""
    from app.coding.engine import CodingEngine
    engine = CodingEngine()
    return await engine.generate_tool(body.description)
