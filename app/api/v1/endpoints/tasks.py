"""
Nexus - Task Endpoints.

Includes tree search planning endpoints for complex task decomposition,
ReACT report generation endpoint, task event replay, and task cancellation.
"""

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import TaskCreate, TaskResponse
from app.communication.message_bus import get_message_bus
from app.core.dependencies import get_db
from app.models.database import TaskEventModel, TaskModel
from app.services.report_agent import get_report_agent
from app.xskill.tree_search import TreeSearchPlanner

logger = structlog.get_logger(__name__)
router = APIRouter()


# -- Schemas for report generation -----------------------------------------


class ReportGenerateRequest(BaseModel):
    """Request schema cho ReACT report generation."""

    topic: str = Field(..., min_length=1, max_length=2000, description="Report topic")
    requirements: str = Field(default="", max_length=2000, description="Additional requirements")
    max_sections: int = Field(default=5, ge=1, le=10, description="Max report sections")


# -- Schemas for tree search endpoints ------------------------------------


class TreeSearchPlanRequest(BaseModel):
    """Request schema cho tree search planning."""

    task: str = Field(..., min_length=1, max_length=2000, description="Task description")
    context: str = Field(default="", max_length=2000, description="Additional context")
    max_depth: int = Field(default=3, ge=1, le=5, description="Max tree depth")
    branching_factor: int = Field(default=3, ge=2, le=5, description="Children per node")
    beam_width: int = Field(default=2, ge=1, le=5, description="Nodes kept per depth after pruning")


class TreeSearchVisualizeRequest(BaseModel):
    """Request schema cho tree visualization."""

    task: str = Field(..., min_length=1, max_length=2000, description="Task description")
    context: str = Field(default="", max_length=2000, description="Additional context")


@router.get("/")
async def list_tasks(
    status: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Liệt kê tasks, optionally filtered by status and/or session.

    STALE-TASK-FIX: Auto-fails tasks stuck at 'executing' for > 15 min
    so they don't appear as "running" to the frontend forever.
    """
    import datetime as _dt

    stmt = select(TaskModel).order_by(TaskModel.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(TaskModel.status == status)
    if session_id:
        # Use indexed session_id column (added in migration 010).
        # Falls back to metadata JSON for tasks created before migration.
        from sqlalchemy import text, or_
        stmt = stmt.where(
            or_(
                TaskModel.session_id == session_id,
                text("metadata->>'session_id' = :sid").bindparams(sid=session_id),
                text("metadata->>'conversation_id' = :sid2").bindparams(sid2=session_id),
            )
        )
    result = await db.execute(stmt)
    tasks = result.scalars().all()

    # STALE-TASK-FIX: Auto-fail zombie tasks that have been "executing" > 15 min.
    # This prevents frontend from showing endless "Resuming task progress..." for dead tasks.
    # CRITICAL: Only auto-fail if TaskRunner confirms the task is NOT actually running in-memory.
    STALE_TASK_THRESHOLD = _dt.timedelta(minutes=15)
    now = _dt.datetime.utcnow()
    needs_commit = False
    from app.core.task_runner import get_task_runner as _get_runner
    runner = _get_runner()
    for t in tasks:
        if t.status in ("executing", "planning", "decomposing") and t.created_at:
            task_age = now - t.created_at.replace(tzinfo=None)
            if task_age > STALE_TASK_THRESHOLD:
                active = runner.get_active_task(t.session_id) if t.session_id else None
                if active and active.task_id == str(t.id):
                    continue  # Task genuinely running in-memory — don't auto-fail
                t.status = "failed"
                t.error = f"Task timed out (stuck for {int(task_age.total_seconds())}s)"
                needs_commit = True
    if needs_commit:
        await db.commit()

    return [
        {
            "id": str(t.id),
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "session_id": (t.metadata_ or {}).get("session_id")
            or (t.metadata_ or {}).get("conversation_id"),
            "parent_task_id": str(t.parent_task_id) if t.parent_task_id else None,
        }
        for t in tasks
    ]


@router.get("/resumable")
async def get_resumable_tasks(
    session_id: str = Query(default="", description="Filter by session_id"),
) -> dict[str, Any]:
    """Get tasks that can be resumed (have checkpoints and are in a resumable state)."""
    from app.core.dependencies import _session_factory
    if not _session_factory:
        return {"tasks": []}

    from app.core.checkpoint_persistence import CheckpointPersistence
    svc = CheckpointPersistence(_session_factory)
    resumable = await svc.get_resumable_tasks()

    if session_id:
        resumable = [t for t in resumable if t.get("session_id") == session_id]

    return {
        "tasks": [
            {
                "task_id": t["task_id"],
                "title": t.get("task_title", ""),
                "turn_number": t["turn_number"],
                "session_id": t.get("session_id", ""),
                "reason": t.get("reason", ""),
            }
            for t in resumable
        ],
    }


@router.get("/{task_id}")
async def get_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Lấy chi tiết task.

    STALE-TASK-FIX: Also auto-marks tasks stuck at 'executing' for >15 min as 'failed'.
    This prevents zombie tasks that block frontend progress display.
    """
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # STALE-TASK-FIX: Auto-fail tasks stuck at "executing" for > 15 minutes.
    # This handles the case where the orchestrator crashed/timed out but never
    # updated the task status (e.g., OOM, process restart, unhandled exception).
    # CRITICAL: Only auto-fail if TaskRunner confirms the task is NOT actually running.
    # Previously used created_at which killed legitimate long-running tasks (>15 min).
    import datetime as _dt
    from app.core.task_runner import get_task_runner as _get_task_runner
    STALE_TASK_THRESHOLD = _dt.timedelta(minutes=15)
    if task.status in ("executing", "planning", "decomposing") and task.created_at:
        task_age = _dt.datetime.utcnow() - task.created_at.replace(tzinfo=None)
        if task_age > STALE_TASK_THRESHOLD:
            # Check in-memory TaskRunner BEFORE marking as failed.
            # If task is still actively running, do NOT auto-fail.
            runner = _get_task_runner()
            active = runner.get_active_task(task.session_id) if task.session_id else None
            if active and active.task_id == str(task.id):
                pass  # Task genuinely running — don't touch
            else:
                task.status = "failed"
                task.error = f"Task timed out (stuck at executing for {int(task_age.total_seconds())}s)"
                await db.commit()
                # GREENLET-FIX: After commit, all attributes are expired.
                # Accessing them triggers lazy-load which fails in async context
                # (MissingGreenlet error). Refresh to reload all attributes.
                await db.refresh(task)

    # Enrich subtask status from DB children (realtime rollup)
    enriched_result = task.result
    if enriched_result and task.parent_task_id is None:
        enriched_result = await _enrich_subtasks_status(db, task)

    return {
        "id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "result": enriched_result,
        "error": task.error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "subtasks": await _get_subtasks_summary(db, task.id),
    }


@router.get("/{task_id}/events")
async def get_task_events(
    task_id: UUID,
    session_id: str = Query(None, description="Session ID for ownership validation"),
    after_seq: int = Query(0, ge=0, description="Return events after this sequence number (incremental fetch)"),
    limit: int = Query(500, ge=1, le=2000, description="Max events to return"),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Get execution events for a task (for replay/history).

    Supports incremental fetch via `after_seq` parameter — frontend polls
    every 1s during active tasks and only needs NEW events since last fetch.
    This prevents O(N) payload growth during long debates (5000+ events).

    Orders by sequence (deterministic per-task counter) when available,
    falling back to created_at + id for legacy events without sequence.
    """
    # H3-FIX: Validate task belongs to the requesting session to prevent cross-session leakage
    if session_id:
        task_check = await db.execute(
            select(TaskModel.id).where(
                TaskModel.id == task_id,
                TaskModel.session_id == session_id,
            )
        )
        if not task_check.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Task not found")

    # BUG-1 FIX: Support incremental fetch with after_seq filter.
    # Frontend sends after_seq=<last_known_sequence> to get only new events.
    # Without this, every 1s poll returns ALL events (O(N) per poll = O(N²) total).
    stmt = select(TaskEventModel).where(TaskEventModel.task_id == task_id)

    if after_seq > 0:
        stmt = stmt.where(TaskEventModel.sequence > after_seq)

    stmt = stmt.order_by(
        TaskEventModel.sequence.asc().nullslast(),
        TaskEventModel.created_at.asc(),
        TaskEventModel.id.asc(),
    ).limit(limit)

    result = await db.execute(stmt)
    events = result.scalars().all()

    # EVENT-ORDER-FIX: Build event list with started_at for proper ordering.
    # Events should be displayed in the order they were INITIATED (started_at),
    # not when they completed (created_at). This matters for parallel tool
    # execution where completion order differs from initiation order.
    event_list = []
    for idx, e in enumerate(events):
        data = e.data or {}
        started_at = data.get("started_at")
        event_list.append({
            "id": str(e.id),
            "task_id": str(e.task_id),
            "session_id": e.session_id,
            "event_type": e.event_type,
            "data": data,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "started_at": started_at,
            "sequence": e.sequence if e.sequence is not None else idx,
        })

    # EVENT-ORDER-FIX: Sort by sequence (monotonic counter, always correct order).
    # sequence is assigned at the moment the event is emitted in the react loop,
    # so it reflects the true chronological order of execution.
    # started_at is only used for DISPLAY timestamp, NOT for reordering.
    event_list.sort(key=lambda ev: (ev["sequence"], 0))

    return event_list


@router.get("/{task_id}/knowledge")
async def get_task_knowledge(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get knowledge graph entities extracted from task results."""
    from app.models.database import KnowledgeNodeModel, KnowledgeEdgeModel

    # Fetch nodes linked to this task (stored in properties.task_id)
    node_stmt = select(KnowledgeNodeModel).where(
        text("properties->>'task_id' = :tid").bindparams(tid=str(task_id))
    ).order_by(KnowledgeNodeModel.created_at.desc()).limit(200)

    node_result = await db.execute(node_stmt)
    nodes_raw = node_result.scalars().all()
    node_ids = {n.id for n in nodes_raw}

    nodes = [
        {
            "id": str(n.id),
            "name": n.name,
            "entity_type": n.entity_type,
            "description": n.description or "",
            "properties": n.properties or {},
        }
        for n in nodes_raw
    ]

    # Fetch edges between these nodes
    edges = []
    if node_ids:
        edge_stmt = select(KnowledgeEdgeModel).where(
            KnowledgeEdgeModel.source_id.in_(node_ids),
            KnowledgeEdgeModel.target_id.in_(node_ids),
        )
        edge_result = await db.execute(edge_stmt)
        edges_raw = edge_result.scalars().all()
        edges = [
            {
                "id": str(e.id),
                "source": str(e.source_id),
                "target": str(e.target_id),
                "relation_type": e.relation_type,
                "weight": e.weight,
            }
            for e in edges_raw
        ]

    return {
        "task_id": str(task_id),
        "nodes": nodes,
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "entity_types": list({n["entity_type"] for n in nodes}),
        },
    }


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Request cancellation of a running task (cross-process safe).

    Three legs, because the task may run on a different Uvicorn worker than the
    one serving this request:
      1. Write a durable cancel flag to the task row (backstop — the pipeline
         re-checks it at each turn boundary even if the pub/sub message is lost).
      2. Try a local cancel via TaskRunner (instant if the task is on THIS
         worker — performs the real asyncio_task.cancel()).
      3. Publish on the Redis cancel channel so the OWNING worker cancels too.

    The final 'cancelled' status is written by the owning worker when it
    finalises the task — this endpoint only requests it.
    """
    import datetime as _dt
    from app.core.cancel_signal import CANCEL_CHANNEL, build_cancel_payload

    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in ("executing", "planning", "decomposing"):
        return {
            "status": "not_running",
            "task_id": str(task_id),
            "message": f"Task is not running (status: {task.status})",
        }

    # 1. Durable cancel flag (backstop, survives worker restarts).
    meta = dict(task.metadata_ or {})
    meta["cancel_requested"] = True
    meta["cancel_requested_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    task.metadata_ = meta
    await db.commit()

    # 2. Local fast-path: cancel immediately if the task lives on this worker.
    local = False
    try:
        from app.core.task_runner import get_task_runner
        runner = get_task_runner()
        if task.session_id:
            local = await runner.cancel_task(task.session_id)
    except Exception as exc:
        logger.warning("cancel_local_failed", task_id=str(task_id), error=str(exc))

    # 3. Cross-process publish so the owning worker (if not this one) cancels.
    if not local:
        try:
            bus = await get_message_bus()
            await bus.publish(
                CANCEL_CHANNEL,
                build_cancel_payload(str(task_id), task.session_id),
            )
        except Exception as exc:
            logger.warning("cancel_publish_failed", task_id=str(task_id), error=str(exc))

    return {"status": "cancelling", "task_id": str(task_id), "local": local}


# -- Resume Task Endpoint ---------------------------------------------------


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Resume a failed/cancelled/crashed task from its last checkpoint.

    Uses the checkpoint system to restore ExecutionState and continue
    from the last completed turn — no repeated work.
    Falls back to context-aware restart if no checkpoint exists.
    """
    result = await db.execute(select(TaskModel).where(TaskModel.id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status not in ("cancelled", "failed", "error", "suspended", "crashed"):
        raise HTTPException(
            status_code=400,
            detail=f"Task cannot be resumed (status: {task.status}). "
                   f"Only cancelled/failed/suspended/crashed tasks can be resumed.",
        )

    # Load checkpoint if available
    from app.core.dependencies import _session_factory
    from app.core.checkpoint_persistence import CheckpointPersistence

    checkpoint = None
    if _session_factory:
        svc = CheckpointPersistence(_session_factory)
        checkpoint = await svc.load_latest_checkpoint(str(task_id))

    # Reset task status
    task.status = "executing"
    task.error = None
    # Clear the durable cancel flag — otherwise the pipeline's DB-flag backstop
    # (_is_cancelled_db) would re-cancel this resumed task at the first turn
    # boundary. Build a NEW dict so SQLAlchemy flags the JSON column dirty.
    from app.core.cancel_signal import clear_cancel_flags

    meta = clear_cancel_flags(task.metadata_ or {})
    meta["resumed"] = True
    meta["resume_count"] = (meta.get("resume_count") or 0) + 1
    meta["resumed_from_turn"] = checkpoint.get("turn_number") if checkpoint else 0
    task.metadata_ = meta
    await db.commit()

    # Use TaskRunner for proper background execution with WS/event support
    try:
        from app.core.task_runner import get_task_runner
        runner = get_task_runner()

        session_id = task.session_id or str(task_id)
        user_id = task.user_id or "default_user"

        resumed_task_id = await runner.resume_task(
            session_id=session_id,
            user_id=user_id,
            task_id=str(task_id),
            checkpoint=checkpoint or {
                "turn_number": 0,
                "state_json": {
                    "task_id": str(task_id),
                    "user_message": task.title or "",
                    "current_turn": 0,
                    "total_agents": [],
                    "status": "executing",
                    "collected_data_cache": [],
                    "failure_tracker": {},
                    "turn_count": 0,
                },
            },
        )

        return {
            "status": "resumed",
            "task_id": str(task_id),
            "resumed_from_turn": checkpoint.get("turn_number", 0) if checkpoint else 0,
            "has_checkpoint": checkpoint is not None,
            "message": (
                f"Task resumed from turn {checkpoint['turn_number']}"
                if checkpoint
                else "Task restarted (no checkpoint available)"
            ),
        }

    except Exception as e:
        task.status = "failed"
        task.error = f"Resume failed: {str(e)[:200]}"
        await db.commit()
        return {
            "status": "resume_failed",
            "task_id": str(task_id),
            "error": str(e)[:300],
        }


# -- Report Generation Endpoint --------------------------------------------


@router.post("/report")
async def generate_report(
    request: ReportGenerateRequest,
) -> dict[str, Any]:
    """
    Generate a research report using the ReACT Report Agent.

    The agent autonomously:
    1. Plans an outline with sections
    2. Runs ReACT loops (Thought → Action → Observation) per section
    3. Uses web_search, fetch_url, and other tools for real data
    4. Synthesizes all sections into a full markdown report
    5. Generates an executive summary

    Returns the full report with metadata and trajectory for XSkill.
    """
    agent = get_report_agent()

    try:
        report = await agent.generate_report(
            topic=request.topic,
            requirements=request.requirements,
            max_sections=request.max_sections,
        )
    except Exception as e:
        logger.error("report_endpoint_failed", topic=request.topic[:100], error=str(e))
        raise HTTPException(status_code=500, detail=f"Report generation failed: {e}")

    return {
        "topic": report.topic,
        "executive_summary": report.executive_summary,
        "sections": [
            {
                "title": s.title,
                "content": s.content,
                "sources": s.sources,
                "tool_calls_count": s.tool_calls_count,
            }
            for s in report.sections
        ],
        "full_markdown": report.full_markdown,
        "metadata": report.metadata,
        "trajectory": report.trajectory,
    }


# -- Tree Search Planning Endpoints ----------------------------------------


@router.post("/plan")
async def tree_search_plan(
    request: TreeSearchPlanRequest,
) -> dict[str, Any]:
    """
    Tree search planning cho complex tasks.

    Explore nhiều approaches song song qua beam search,
    score bằng LLM-as-judge, và chọn optimal path.

    Returns tree structure, best path, và executable steps.
    """
    planner = TreeSearchPlanner(
        max_depth=request.max_depth,
        branching_factor=request.branching_factor,
        beam_width=request.beam_width,
    )

    try:
        tree = await planner.plan(task=request.task, context=request.context)
        steps = planner.plan_to_steps(tree)
        best_path_nodes = [
            tree.nodes[nid].to_dict()
            for nid in tree.best_path
            if nid in tree.nodes
        ]
    except Exception as e:
        logger.error("tree_search_plan_failed", task=request.task[:100], error=str(e))
        raise HTTPException(status_code=500, detail=f"Tree search planning failed: {e}")

    return {
        "tree": tree.to_dict(),
        "best_path": best_path_nodes,
        "steps": steps,
    }


@router.post("/plan/visualize")
async def tree_search_visualize(
    request: TreeSearchVisualizeRequest,
) -> dict[str, Any]:
    """
    Tree search planning với markdown visualization.

    Chạy planning pipeline và trả về cả tree dict lẫn
    human-readable markdown representation.
    """
    planner = TreeSearchPlanner()

    try:
        tree = await planner.plan(task=request.task, context=request.context)
        planner.plan_to_steps(tree)  # Ensure best_path is populated
    except Exception as e:
        logger.error("tree_search_visualize_failed", task=request.task[:100], error=str(e))
        raise HTTPException(
            status_code=500,
            detail=f"Tree search visualization failed: {e}",
        )

    return {
        "markdown": tree.to_markdown(),
        "tree": tree.to_dict(),
    }


# ══════════════════════════════════════════════════════════════════════
# Subtask Rollup Helpers
# ══════════════════════════════════════════════════════════════════════


async def _get_subtasks_summary(
    db: AsyncSession, parent_id: UUID
) -> list[dict[str, Any]]:
    """Get realtime status summary of all child tasks."""
    stmt = (
        select(TaskModel)
        .where(TaskModel.parent_task_id == parent_id)
        .order_by(TaskModel.created_at.asc())
    )
    result = await db.execute(stmt)
    children = result.scalars().all()
    if not children:
        return []
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "status": c.status,
            "error": c.error,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in children
    ]


async def _enrich_subtasks_status(
    db: AsyncSession, task: TaskModel
) -> dict[str, Any] | None:
    """Enrich task.result subtasks with realtime status from DB children.

    When orchestrator decomposes a task, result.subtasks contains the plan.
    But those statuses are stale (set at decomposition time).
    This merges live status from child TaskModel rows into the result dict.
    """
    result = task.result
    if not result or not isinstance(result, dict):
        return result

    # Find the subtask list in result (multiple possible shapes)
    subtask_list = None
    subtask_key = None
    if isinstance(result.get("subtasks"), list):
        subtask_list = result["subtasks"]
        subtask_key = "subtasks"
    elif isinstance(result.get("plan"), dict):
        plan = result["plan"]
        if isinstance(plan.get("steps"), list):
            subtask_list = plan["steps"]
            subtask_key = "plan.steps"
        elif isinstance(plan.get("subtasks"), list):
            subtask_list = plan["subtasks"]
            subtask_key = "plan.subtasks"
    elif isinstance(result.get("steps"), list):
        subtask_list = result["steps"]
        subtask_key = "steps"

    if not subtask_list:
        return result

    # Fetch live child statuses
    stmt = (
        select(TaskModel.title, TaskModel.status, TaskModel.result, TaskModel.error)
        .where(TaskModel.parent_task_id == task.id)
        .order_by(TaskModel.created_at.asc())
    )
    db_result = await db.execute(stmt)
    children = db_result.all()

    if not children:
        return result

    # Build title→status map for matching
    child_status_map: dict[str, dict[str, Any]] = {}
    for idx, child in enumerate(children):
        child_status_map[child.title.lower().strip()] = {
            "status": child.status,
            "error": child.error,
            "result_summary": _summarize_result(child.result),
        }
        # Also map by index for positional matching
        child_status_map[f"__idx_{idx}"] = child_status_map[child.title.lower().strip()]

    # Merge live status into subtask entries
    enriched = dict(result)  # shallow copy
    for idx, item in enumerate(subtask_list):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", item.get("name", ""))).lower().strip()
        matched = child_status_map.get(title) or child_status_map.get(f"__idx_{idx}")
        if matched:
            item["status"] = matched["status"]
            if matched["error"]:
                item["error"] = matched["error"]
            if matched["result_summary"]:
                item["result_summary"] = matched["result_summary"]

    return enriched


def _summarize_result(result: dict | None) -> str | None:
    """Extract a brief summary from a subtask's result."""
    if not result:
        return None
    if isinstance(result.get("summary"), str):
        return result["summary"][:200]
    if isinstance(result.get("content"), str):
        return result["content"][:200]
    return None
