"""
Nexus - Analytics API.

Token usage, cost tracking, model breakdown, and usage patterns.
Queries conversation_messages table for aggregated analytics data.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.models.database import ConversationMessageModel

logger = structlog.get_logger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Cost lookup (mirrors frontend TokenStatusBar pricing)
# ------------------------------------------------------------------

COST_PER_1K: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "claude-sonnet-4-20250514": {"input": 0.003, "output": 0.015},
    "claude-haiku-3": {"input": 0.00025, "output": 0.00125},
    "gemini-2.0-flash": {"input": 0.000075, "output": 0.0003},
}

DEFAULT_COST_PER_1K = {"input": 0.002, "output": 0.008}

PERIOD_DAYS: dict[str, int] = {
    "1d": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------


class AnalyticsSummary(BaseModel):
    total_tokens: int = 0
    total_cost: float = 0.0
    total_sessions: int = 0
    total_messages: int = 0
    avg_tokens_per_message: int = 0


class DailyUsage(BaseModel):
    date: str
    tokens: int = 0
    cost: float = 0.0
    messages: int = 0


class ModelBreakdown(BaseModel):
    model: str
    tokens: int = 0
    cost: float = 0.0
    percentage: int = 0


class TopSession(BaseModel):
    session_id: str
    title: str = ""
    tokens: int = 0
    messages: int = 0


class HourlyPattern(BaseModel):
    hour: int
    tokens: int = 0


class AnalyticsResponse(BaseModel):
    period: str
    summary: AnalyticsSummary
    daily_usage: list[DailyUsage] = Field(default_factory=list)
    model_breakdown: list[ModelBreakdown] = Field(default_factory=list)
    top_sessions: list[TopSession] = Field(default_factory=list)
    hourly_pattern: list[HourlyPattern] = Field(default_factory=list)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _calculate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None,
) -> float:
    """Calculate cost for given tokens using model-specific pricing."""
    pricing = COST_PER_1K.get(model or "", DEFAULT_COST_PER_1K)
    return (
        (prompt_tokens / 1000) * pricing["input"]
        + (completion_tokens / 1000) * pricing["output"]
    )


def _extract_usage(metadata: dict | None) -> tuple[int, int, int, str | None]:
    """Extract (prompt, completion, total, model) from message metadata.

    Returns (0, 0, 0, None) when usage data is absent.
    """
    if not metadata:
        return 0, 0, 0, None

    usage = metadata.get("usage")
    model = metadata.get("model")
    model_str = str(model) if model else None

    if not isinstance(usage, dict):
        return 0, 0, 0, model_str

    prompt = int(usage.get("prompt_tokens", 0) or 0)
    completion = int(usage.get("completion_tokens", 0) or 0)
    total = int(usage.get("total_tokens", 0) or 0)

    if total == 0:
        total = prompt + completion

    return prompt, completion, total, model_str


# ------------------------------------------------------------------
# Endpoint
# ------------------------------------------------------------------


@router.get(
    "/",
    summary="Get analytics data for token usage, cost, and model breakdown",
    response_model=AnalyticsResponse,
)
async def get_analytics(
    period: str = Query("7d", description="Period: 1d, 7d, 30d, 90d"),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    db: AsyncSession = Depends(get_db),
) -> AnalyticsResponse:
    """
    Aggregate analytics from conversation_messages metadata.

    Extracts token usage from metadata->usage JSON field,
    calculates costs using the same pricing as TokenStatusBar,
    and returns daily/hourly breakdowns.
    """
    days = PERIOD_DAYS.get(period, 7)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Build base query — only assistant messages have usage metadata
    stmt = (
        select(ConversationMessageModel)
        .where(
            ConversationMessageModel.created_at >= cutoff,
            ConversationMessageModel.role == "assistant",
        )
        .order_by(ConversationMessageModel.created_at.asc())
    )

    if session_id:
        stmt = stmt.where(ConversationMessageModel.session_id == session_id)

    try:
        result = await db.execute(stmt)
        messages = result.scalars().all()
    except Exception as exc:
        logger.error("analytics_query_error", error=str(exc))
        return AnalyticsResponse(
            period=period,
            summary=AnalyticsSummary(),
        )

    # Also count total messages (user + assistant) for the period
    count_stmt = (
        select(func.count(ConversationMessageModel.id))
        .where(
            ConversationMessageModel.created_at >= cutoff,
            ConversationMessageModel.role.in_(["user", "assistant"]),
        )
    )
    if session_id:
        count_stmt = count_stmt.where(
            ConversationMessageModel.session_id == session_id
        )

    try:
        total_msg_result = await db.execute(count_stmt)
        total_messages = total_msg_result.scalar() or 0
    except Exception:
        total_messages = len(messages) * 2  # Rough estimate

    # ------------------------------------------------------------------
    # Aggregate in Python
    # ------------------------------------------------------------------

    total_tokens = 0
    total_cost = 0.0
    session_ids: set[str] = set()

    # Daily aggregation
    daily_map: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {"tokens": 0, "cost": 0.0, "messages": 0}
    )

    # Model aggregation
    model_map: dict[str, dict[str, int | float]] = defaultdict(
        lambda: {"tokens": 0, "cost": 0.0, "prompt": 0, "completion": 0}
    )

    # Session aggregation
    session_map: dict[str, dict[str, int | str]] = defaultdict(
        lambda: {"tokens": 0, "messages": 0, "title": ""}
    )

    # Hourly pattern
    hourly_map: dict[int, int] = defaultdict(int)

    for msg in messages:
        metadata = msg.metadata_ if hasattr(msg, "metadata_") else None
        prompt, completion, total, model = _extract_usage(metadata)

        if total == 0:
            continue

        cost = _calculate_cost(prompt, completion, model)
        total_tokens += total
        total_cost += cost
        session_ids.add(msg.session_id)

        # Daily
        date_str = msg.created_at.strftime("%Y-%m-%d") if msg.created_at else "unknown"
        daily_map[date_str]["tokens"] += total
        daily_map[date_str]["cost"] = float(daily_map[date_str]["cost"]) + cost
        daily_map[date_str]["messages"] += 1

        # Model
        model_key = model or "unknown"
        model_map[model_key]["tokens"] += total
        model_map[model_key]["cost"] = float(model_map[model_key]["cost"]) + cost
        model_map[model_key]["prompt"] += prompt
        model_map[model_key]["completion"] += completion

        # Session
        session_map[msg.session_id]["tokens"] = (
            int(session_map[msg.session_id]["tokens"]) + total
        )
        session_map[msg.session_id]["messages"] = (
            int(session_map[msg.session_id]["messages"]) + 1
        )

        # Hourly
        if msg.created_at:
            hourly_map[msg.created_at.hour] += total

    # Build session titles from first user message per session
    session_title_map: dict[str, str] = {}
    if session_ids:
        try:
            title_stmt = (
                select(ConversationMessageModel)
                .where(
                    ConversationMessageModel.session_id.in_(list(session_ids)),
                    ConversationMessageModel.role.in_(["user", "title"]),
                )
                .order_by(ConversationMessageModel.created_at.asc())
            )
            title_result = await db.execute(title_stmt)
            title_messages = title_result.scalars().all()

            for tmsg in title_messages:
                sid = tmsg.session_id
                if sid not in session_title_map:
                    content = tmsg.content or ""
                    session_title_map[sid] = content[:80]
        except Exception:
            pass  # Titles are optional

    # ------------------------------------------------------------------
    # Build response
    # ------------------------------------------------------------------

    msg_with_usage = sum(1 for m in messages if _extract_usage(
        m.metadata_ if hasattr(m, "metadata_") else None
    )[2] > 0)

    summary = AnalyticsSummary(
        total_tokens=total_tokens,
        total_cost=round(total_cost, 4),
        total_sessions=len(session_ids),
        total_messages=total_messages,
        avg_tokens_per_message=(
            total_tokens // msg_with_usage if msg_with_usage > 0 else 0
        ),
    )

    # Daily usage sorted by date
    daily_usage = sorted(
        [
            DailyUsage(
                date=date,
                tokens=int(data["tokens"]),
                cost=round(float(data["cost"]), 4),
                messages=int(data["messages"]),
            )
            for date, data in daily_map.items()
        ],
        key=lambda d: d.date,
    )

    # Model breakdown with percentages
    model_breakdown = []
    for model_name, data in sorted(
        model_map.items(), key=lambda x: x[1]["tokens"], reverse=True
    ):
        pct = (
            round(int(data["tokens"]) / total_tokens * 100)
            if total_tokens > 0
            else 0
        )
        model_breakdown.append(
            ModelBreakdown(
                model=model_name,
                tokens=int(data["tokens"]),
                cost=round(float(data["cost"]), 4),
                percentage=pct,
            )
        )

    # Top sessions sorted by tokens desc, limit 10
    top_sessions = sorted(
        [
            TopSession(
                session_id=sid,
                title=session_title_map.get(sid, f"Session {sid[:8]}..."),
                tokens=int(data["tokens"]),
                messages=int(data["messages"]),
            )
            for sid, data in session_map.items()
        ],
        key=lambda s: s.tokens,
        reverse=True,
    )[:10]

    # Hourly pattern (fill all 24 hours)
    hourly_pattern = [
        HourlyPattern(hour=h, tokens=hourly_map.get(h, 0)) for h in range(24)
    ]

    return AnalyticsResponse(
        period=period,
        summary=summary,
        daily_usage=daily_usage,
        model_breakdown=model_breakdown,
        top_sessions=top_sessions,
        hourly_pattern=hourly_pattern,
    )
