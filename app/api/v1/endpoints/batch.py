"""
Nexus - Batch Runner API Endpoints.

POST /api/v1/batch/run    — start a new batch or resume an existing one
GET  /api/v1/batch/{id}/status — check batch progress
"""

from __future__ import annotations

from typing import Optional

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/batch")


# ------------------------------------------------------------------
# Request / Response schemas
# ------------------------------------------------------------------

class BatchRunRequest(BaseModel):
    """Request body for POST /batch/run."""
    prompts: list[str] = Field(..., min_length=1, description="List of prompts to process")
    batch_id: Optional[str] = Field(None, description="Optional batch ID (resume if exists)")
    max_concurrent: int = Field(3, ge=1, le=20, description="Max concurrent executions")


class BatchStatusResponse(BaseModel):
    """Response for GET /batch/{id}/status."""
    batch_id: str
    total: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0
    running: int = 0
    progress_pct: float = 0.0


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/run")
async def run_batch(request: BatchRunRequest):
    """Start or resume a batch of prompts.

    If ``batch_id`` matches an existing checkpoint, the batch resumes
    from where it left off.  Otherwise a new batch is created.
    """
    from app.services.llm_client import get_core_llm
    from app.core.batch_runner import BatchRunner

    try:
        llm = get_core_llm()
        runner = BatchRunner(llm=llm, max_concurrent=request.max_concurrent)
        result = await runner.run_batch(
            prompts=request.prompts,
            batch_id=request.batch_id,
        )
        return result.to_dict()
    except Exception as exc:
        logger.error("batch_run_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Batch execution failed: {exc}")


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
async def batch_status(batch_id: str):
    """Check current status of a batch run."""
    from app.services.llm_client import get_core_llm
    from app.core.batch_runner import BatchRunner

    llm = get_core_llm()
    runner = BatchRunner(llm=llm)
    status = runner.get_batch_status(batch_id)

    if "error" in status:
        raise HTTPException(status_code=404, detail=status["error"])

    return status
