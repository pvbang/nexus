"""
MekongAI Nexus - System Endpoints.

System info, stats, Docker sandbox management, and maintenance.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import __app_name__, __version__
from app.config import get_settings
from app.core.circuit_breaker import get_circuit_breaker
from app.core.dependencies import get_db
from app.memory.manager import MemoryManager
from app.models.database import TaskModel
from app.sandbox.manager import SandboxManager

logger = structlog.get_logger(__name__)
router = APIRouter()
settings = get_settings()


@router.get("/info")
async def system_info() -> dict[str, Any]:
    """Thông tin hệ thống."""
    return {
        "app": __app_name__,
        "version": __version__,
        "env": settings.app_env,
        "core_llm": settings.core_llm_model,
        "agent_llm": settings.agent_llm_model,
        "local_llm_enabled": settings.local_llm_enabled,
        "docker_sandbox": settings.docker_sandbox_enabled,
        "ironclaw_enabled": settings.ironclaw_enabled,
    }


@router.get("/stats")
async def system_stats() -> dict[str, Any]:
    """Thống kê hệ thống — with timeout to prevent 524 on slow DB."""
    import asyncio

    from app.core.dependencies import _session_factory

    async def _get_counts() -> dict[str, int]:
        """Run all COUNT queries via a dedicated session."""
        try:
            async with _session_factory() as session:
                result = await asyncio.wait_for(
                    session.execute(text(
                        "SELECT "
                        "(SELECT COUNT(*) FROM agents) AS agents, "
                        "(SELECT COUNT(*) FROM tasks) AS tasks, "
                        "(SELECT COUNT(*) FROM messages) AS messages"
                    )),
                    timeout=10.0,
                )
                row = result.one()
                return {"agents": row[0] or 0, "tasks": row[1] or 0, "messages": row[2] or 0}
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("stats_count_query_failed", error=str(e))
            return {"agents": 0, "tasks": 0, "messages": 0}

    async def _get_graph_stats() -> dict[str, Any]:
        try:
            async with _session_factory() as session:
                memory_mgr = MemoryManager(session)
                return await asyncio.wait_for(memory_mgr.get_graph_stats(), timeout=8.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("stats_graph_query_failed", error=str(e))
            return {"error": "timeout"}

    async def _get_xskill_stats() -> dict[str, Any]:
        try:
            async with _session_factory() as session:
                from app.xskill.engine import ContinualLearningEngine
                xskill = ContinualLearningEngine(session)
                return await asyncio.wait_for(xskill.get_stats(), timeout=8.0)
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning("stats_xskill_query_failed", error=str(e))
            return {"error": "timeout"}

    counts, graph_stats, xskill_stats = await asyncio.gather(
        _get_counts(), _get_graph_stats(), _get_xskill_stats()
    )

    return {
        "agents": {"total": counts["agents"]},
        "tasks": {"total": counts["tasks"]},
        "messages": {"total": counts["messages"]},
        "knowledge_graph": graph_stats,
        "xskill": xskill_stats,
    }


# ------------------------------------------------------------------
# Sandbox Management
# ------------------------------------------------------------------


@router.get("/sandbox/health", summary="Docker sandbox health check")
async def sandbox_health() -> dict[str, Any]:
    """
    Check Docker daemon accessibility and sandbox readiness.

    Returns docker_available, docker_version, sandbox_image_ready.
    """
    manager = SandboxManager()
    return await manager.health_check()


@router.get("/sandbox/configs", summary="List sandbox configurations")
async def sandbox_configs() -> dict[str, Any]:
    """List available Docker sandbox configurations (presets + default)."""
    manager = SandboxManager()
    configs = manager.list_sandbox_configs()
    return {
        "configs": configs,
        "total": len(configs),
        "docker_enabled": settings.docker_sandbox_enabled,
    }


# ------------------------------------------------------------------
# Stale Task Cleanup (BUG-1 / BUG-20 fix)
# ------------------------------------------------------------------


@router.post("/cleanup-stale-tasks", summary="Clean up stale executing tasks")
async def cleanup_stale_tasks(
    max_age_minutes: int = 30,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Mark tasks stuck in 'executing' status for > max_age_minutes as 'failed'.

    This prevents orphaned tasks from polluting the UI and counts.
    Automatically cleans up tasks that lost their pipeline (server restart,
    timeout, crash, etc.)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

    result = await db.execute(
        update(TaskModel)
        .where(
            TaskModel.status == "executing",
            TaskModel.created_at < cutoff,
        )
        .values(status="failed", error=f"Stale task cleanup (stuck > {max_age_minutes} min)")
        .returning(TaskModel.id)
    )
    cleaned_ids = [str(row[0]) for row in result.fetchall()]
    await db.commit()

    if cleaned_ids:
        logger.info("stale_tasks_cleaned", count=len(cleaned_ids), ids=cleaned_ids[:10])

    return {
        "cleaned": len(cleaned_ids),
        "task_ids": cleaned_ids,
        "max_age_minutes": max_age_minutes,
    }


# ------------------------------------------------------------------
# Knowledge Graph Visualization
# ------------------------------------------------------------------


@router.get("/graph", summary="Knowledge graph data for visualization")
async def get_graph_data(
    limit: int = 500,
    entity_type: str = "",
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """
    Return full knowledge graph (nodes + edges) for frontend visualization.

    Query params:
    - limit: max nodes to return (default 500)
    - entity_type: filter by entity type (empty = all)
    """
    memory_mgr = MemoryManager(db)
    return await memory_mgr.get_full_graph(
        limit_nodes=min(limit, 2000),
        entity_type=entity_type or None,
    )


@router.get("/graph/node/{node_id}", summary="Node detail with relations")
async def get_node_detail(
    node_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get detailed info for a single node including all its relations."""
    import uuid as _uuid

    memory_mgr = MemoryManager(db)
    try:
        uid = _uuid.UUID(node_id)
    except ValueError:
        return {"error": "Invalid node_id format"}

    relations = await memory_mgr.get_node_relations(uid, direction="both")
    return {
        "node_id": node_id,
        "relations": relations,
        "relation_count": len(relations),
    }


# ------------------------------------------------------------------
# Circuit Breaker Status
# ------------------------------------------------------------------


@router.get("/circuit-breakers", summary="LLM provider circuit breaker status")
async def get_circuit_status() -> dict[str, Any]:
    """
    Return circuit breaker state for all tracked LLM providers.

    Each provider shows: state (closed/open/half_open), failure_count,
    last_failure timestamp, and configured thresholds.
    """
    breaker = get_circuit_breaker()
    return {
        "circuits": breaker.get_status(),
    }


@router.post("/reload-config", summary="Reload .env settings at runtime (operator action)")
async def reload_config() -> dict[str, Any]:
    """Re-read ``.env`` and rebuild the settings singleton WITHOUT a restart.

    For a deliberate operator change to non-credential config — switch LLM
    provider/model, rotate a feature API key, flip a feature flag. Re-loads .env
    (override=True, so the edited file wins), clears the get_settings cache, and
    re-binds this module's ``settings`` snapshot so /info reflects the new values.

    NOTE: live AWS session credentials are not clobbered — a token from
    refresh-aws.sh / the environment still outranks .env, and the refresher keeps
    rotating it. Modules that captured ``settings = get_settings()`` at import
    time keep their snapshot until they re-read; this is a bounded reload.
    """
    from app.config import reload_settings
    global settings
    fresh = reload_settings()
    settings = fresh
    logger.info(
        "config_reloaded",
        core_provider=fresh.core_llm_provider,
        core_model=fresh.core_llm_model,
        agent_provider=fresh.agent_llm_provider,
        agent_model=fresh.agent_llm_model,
    )
    return {
        "status": "reloaded",
        "core_llm_provider": fresh.core_llm_provider,
        "core_llm_model": fresh.core_llm_model,
        "agent_llm_provider": fresh.agent_llm_provider,
        "agent_llm_model": fresh.agent_llm_model,
        "note": (
            "Settings re-read from .env. Modules holding an import-time snapshot "
            "keep theirs until they re-read; AWS session creds are unaffected."
        ),
    }


@router.post("/circuit-breakers/{provider}/reset", summary="Reset circuit breaker for a provider")
async def reset_circuit_breaker(provider: str) -> dict[str, Any]:
    """
    Manually reset a tripped circuit breaker for a provider (admin action).

    Use when you know the provider has recovered but the circuit hasn't
    automatically transitioned back to CLOSED yet.
    """
    breaker = get_circuit_breaker()
    breaker.reset(provider)
    return {
        "provider": provider,
        "action": "reset",
        "new_state": "closed",
    }


# ------------------------------------------------------------------
# Background Task Metrics
# ------------------------------------------------------------------


@router.get("/background-metrics", summary="Background task success/failure metrics")
async def background_task_metrics() -> dict[str, Any]:
    """
    Return success/failure counts and timestamps for fire-and-forget background tasks.

    Tracks: xskill_accumulate, graph_rag_auto_index, evolution_agent, checkpoint_save,
    phantom_write_no_task_context (a write ran without authoritative task context;
    deduped per session, so the count is sessions affected, not writes).
    Each task shows success_count, failure_count, last_success_time,
    last_failure_time, and last_error_message.
    """
    from app.core.background_metrics import get_background_metrics

    return {
        "tasks": get_background_metrics().get_metrics(),
    }
