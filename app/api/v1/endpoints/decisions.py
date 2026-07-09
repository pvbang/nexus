"""
API endpoints for interactive decision making.

Allows frontend to submit user answers to pending decisions.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import structlog
from app.mcp.tools.interactive import get_pending_decision

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["decisions"])


# ============================================================
# Request/Response Models
# ============================================================


class AnswerRequest(BaseModel):
    """User answer to a decision."""

    value: str = Field(..., min_length=1, max_length=1000, description="Selected value or custom input")


class DecisionResponse(BaseModel):
    """Decision status response."""

    id: str
    status: str
    question: str
    answer: str | None = None


# ============================================================
# Endpoints
# ============================================================


@router.post("/{decision_id}/answer")
async def answer_decision(
    decision_id: str,
    answer: AnswerRequest,
) -> dict[str, str]:
    """
    Submit user answer to a pending decision.

    The agent is waiting for this answer to continue execution.
    After receiving the answer, the agent will resume immediately.

    Args:
        decision_id: ID of the pending decision
        answer: User's selected value (from options or custom input)

    Returns:
        Status confirmation

    Raises:
        404: Decision not found (expired or invalid ID)
        400: Decision already answered or timeout
    """
    decision = get_pending_decision(decision_id)

    if not decision:
        logger.warning("decision_not_found", decision_id=decision_id)
        raise HTTPException(status_code=404, detail="Decision not found or expired")

    if decision.status != "pending":
        # Decision already resolved (timeout or answered by another source).
        # Return success so frontend doesn't loop on errors.
        logger.info(
            "decision_already_resolved",
            decision_id=decision_id,
            status=decision.status,
            existing_answer=decision.answer,
        )
        return {
            "status": "already_resolved",
            "decision_id": decision_id,
            "answer": decision.answer,
            "resolved_by": decision.status,
        }

    # Validate answer if not allowing custom
    if not decision.allow_custom:
        valid_values = {opt["value"] for opt in decision.options}
        if answer.value not in valid_values:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid value. Must be one of: {', '.join(valid_values)}",
            )

    # Set answer and wake up waiting agent
    decision.set_answer(answer.value)

    logger.info(
        "decision_answered_via_api",
        decision_id=decision_id,
        session_id=decision.session_id,
        answer=answer.value,
    )

    return {
        "status": "ok",
        "decision_id": decision_id,
        "answer": answer.value,
    }


@router.get("/{decision_id}")
async def get_decision_status(decision_id: str) -> DecisionResponse:
    """
    Get status of a decision.

    Useful for polling or checking if decision is still pending.

    Args:
        decision_id: ID of the decision

    Returns:
        Decision status and details

    Raises:
        404: Decision not found
    """
    decision = get_pending_decision(decision_id)

    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")

    return DecisionResponse(
        id=decision.id,
        status=decision.status,
        question=decision.question,
        answer=decision.answer,
    )
