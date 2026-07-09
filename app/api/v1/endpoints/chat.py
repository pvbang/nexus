"""
MekongAI Nexus - Chat Endpoints.

Entry point chính cho user tương tác với hệ thống.
Includes both REST (non-streaming) and SSE (streaming) endpoints.
Also exposes Agent Intelligence endpoints (profiles, models).

UNIFIED ARCHITECTURE: SSE /stream endpoint delegates to TaskRunner
which runs CoreOrchestrator in background. SSE is just a "live view"
into the running task — subscribing to events via queue pattern.

Benefits:
- /sessions/{id}/state correctly reports running task for ALL paths
- Multi-tab users see events via Monitor WS (TaskRunner broadcasts)
- F5 recovery works: task continues in background, message persisted
- Single persist path: no dual-persist race conditions
"""

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import ChatRequest, ChatResponse
from app.core.auth import get_optional_user
from app.core.conversation_profiles import get_profile_manager
from app.core.dependencies import get_db
from app.core.model_switcher import get_model_switcher
from app.core.session_mutex import get_session_mutex
from app.core.task_runner import get_task_runner
from app.models.database import TaskModel
from app.services.llm_client import get_core_llm
from app.services.orchestrator import CoreOrchestrator

logger = structlog.get_logger(__name__)
router = APIRouter()


@router.post("/", response_model=None)
async def send_message(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_optional_user),
) -> dict[str, Any]:
    """
    Gửi tin nhắn cho Core Agent (non-streaming).

    Core Agent sẽ phân tích, spawn agents nếu cần,
    điều phối swarm và trả về kết quả.
    """
    orchestrator = CoreOrchestrator(db)
    result = await orchestrator.process_user_request(
        message=request.message,
        user_id=user_id,
        conversation_id=request.conversation_id,
        use_free=request.use_free,
    )
    return result


@router.post("/simple")
async def simple_chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_optional_user),
) -> dict[str, Any]:
    """Simple chat - routes through unified pipeline for security."""
    orchestrator = CoreOrchestrator(db)
    result = await orchestrator.process_user_request(
        message=request.message,
        user_id=user_id,
        conversation_id=request.conversation_id,
        use_free=request.use_free,
    )
    return {
        "message": result.get("message", "No response generated."),
        "conversation_id": result.get("conversation_id", request.conversation_id),
        "status": result.get("status", "completed"),
    }


def _sse_event(data: dict[str, Any], event_id: int | None = None) -> str:
    """Format a dict as an SSE event line.

    When ``event_id`` is provided (the task_events sequence), an ``id:`` line is
    emitted so clients can resume via the ``Last-Event-ID`` header on reconnect.
    """
    from app.core.sse import format_sse_event
    return format_sse_event(data, event_id=event_id)


def _handle_event(event: dict[str, Any]) -> str | None:
    """Process a single task event and return the SSE string to yield.

    Converts enriched TaskRunner events into frontend-expected SSE format.
    """
    event_type = event.get("event_type", "")

    # ── Token streaming (core real-time UX) ──
    if event_type == "token":
        return _sse_event({
            "type": "token",
            "content": event.get("content", ""),
        })

    # ── Orchestrator step progress ──
    if event_type == "step":
        return _sse_event({
            "type": "step",
            "step": event.get("step", 0),
            "total_steps": event.get("total_steps", 0),
            "icon": event.get("icon", "⏳"),
            "title": event.get("title", ""),
            "description": event.get("description", ""),
            "status": event.get("status", "active"),
        })

    # ── Granular debate events (legacy structured) ──
    if event_type in ("debate_proposal", "debate_rebuttal", "debate_verdict"):
        return _sse_event({
            "type": event_type,
            "round": event.get("round", 0),
            "total_rounds": event.get("total_rounds", 0),
            "agent_name": event.get("agent_name", ""),
            "agent_role": event.get("agent_role", ""),
            "content": event.get("content", ""),
            "tools_used": event.get("tools_used", []),
            "verdict": event.get("verdict"),
            "phase": event.get("phase", ""),
        })

    # ── Chat-based debate events (DebateChatRoom) ──
    if event_type in ("debate_started", "debate_message", "debate_concluded",
                      "moderator_intervention"):
        return _sse_event({
            "type": event_type,
            # content-based matching which is less reliable.
            "sequence": event.get("sequence"),
            # Identity & routing
            "session_id": event.get("session_id", ""),
            "conversation_id": event.get("conversation_id", ""),
            # Turn/message fields
            "turn": event.get("turn"),
            "agent_name": event.get("agent_name", ""),
            "agent_role": event.get("agent_role", ""),
            "content": event.get("content", event.get("message", "")),
            "message_type": event.get("message_type", ""),
            # Evidence & tools
            "tools_used": event.get("tools_used", []),
            "evidence_items": event.get("evidence_items", []),
            "references": event.get("references", []),
            "confidence": event.get("confidence"),
            # Lifecycle fields
            "is_final": event.get("is_final"),
            "total_turns": event.get("total_turns"),
            "budget_remaining": event.get("budget_remaining"),
            # Quality metrics
            "quality_score": event.get("quality_score"),
            "convergence_score": event.get("convergence_score"),
            # Moderator fields
            "action_type": event.get("action_type") or event.get("action"),
            # debate_started fields
            "agents": event.get("agents", []),
            "max_turns": event.get("max_turns"),
            "task": event.get("task", ""),
            "strategy": event.get("strategy", ""),
            # debate_concluded fields
            "concluded_by": event.get("concluded_by"),
        })

    # ── Debate tool call/result events (from agent_participant, action_phase, preparation) ──
    if event_type in ("debate_tool_call", "debate_tool_result",
                      "action_tool_call", "action_tool_result",
                      "debate_agent_plan", "preparation_research"):
        return _sse_event({
            "type": event_type,
            "sequence": event.get("sequence"),
            "agent_name": event.get("agent_name", ""),
            "tool": event.get("tool", ""),
            "params_preview": event.get("params_preview", ""),
            "output_preview": event.get("output_preview", ""),
            "success": event.get("success"),
            "turn": event.get("turn"),
            "phase": event.get("phase", ""),
            # debate_agent_plan fields
            "main_argument": event.get("main_argument", ""),
            "evidence_needed": event.get("evidence_needed"),
        })

    # ── Task heartbeat events (from TaskRunner heartbeat loop) ──
    # Frontend uses these to confirm task is still processing.
    if event_type == "task_heartbeat":
        return _sse_event({
            "type": "task_heartbeat",
            "last_activity": event.get("last_activity_desc", ""),
            "current_tool": event.get("current_tool"),
        })

    # ── Micro-step progress events (from TurnExecutor) ──
    if event_type == "micro_step":
        return _sse_event({
            "type": "micro_step",
            "turn": event.get("turn", 0),
            "phase": event.get("phase", ""),
            "agent": event.get("agent", ""),
            "description": event.get("description", ""),
        })

    # ── Agent activity events ──
    if event_type in ("agent_thinking", "agent_response", "tool_start", "tool_done"):
        return _sse_event({
            "type": event_type,
            "agent_name": event.get("agent_name", ""),
            "agent_role": event.get("agent_role", ""),
            "tool": event.get("tool", ""),
            "params": event.get("params"),
            "output": event.get("output"),
            "content": event.get("content"),
            "preview": event.get("preview", ""),
            "output_preview": event.get("output_preview", ""),
            "success": event.get("success"),
            "execution_time_ms": event.get("execution_time_ms"),
        })

    # ── Agent spawn event ──
    if event_type == "agent_spawn":
        return _sse_event({
            "type": "agent_spawn",
            "agent_name": event.get("agent_name", ""),
            "agent_role": event.get("agent_role", ""),
            "system_prompt_preview": event.get("system_prompt_preview", ""),
        })

    # ── Swarm round events ──
    if event_type in ("swarm_round", "swarm_round_start"):
        return _sse_event({
            "type": "swarm_round_start",
            "round": event.get("round", 0),
            "total_rounds": event.get("total_rounds", 0),
            "proposals": event.get("proposals", []),
            "rebuttals": event.get("rebuttals", []),
            "verdict": event.get("verdict"),
            "debater_count": event.get("debater_count", 0),
        })

    # ── Task lifecycle events ──
    if event_type == "task_completed":
        # Terminal — handled separately by SSE generator
        return None

    if event_type == "task_failed":
        # Terminal — handled separately by SSE generator
        return None

    if event_type in ("task_created", "swarm_formed", "xskill_knowledge_found"):
        return _sse_event({
            "type": "event",
            "event_type": event_type,
            "data": event,
        })

    # ── Interactive decision events (ask_user tool) ──
    if event_type in ("user_input_required", "user_input_resolved"):
        return _sse_event({"type": event_type, **{
            k: v for k, v in event.items()
            if k not in ("event_type", "sequence", "task_id", "timestamp")
        }})

    # ── Deep reasoning, planning, synthesis, etc. ──
    # and Monitor WS unable to match events to sessions.
    # Also KEEP sequence in payload (needed for frontend dedup/ordering).
    if event_type in (
        "debate_phase", "debate_critique", "debate_revision", "react_phase",
        "deep_analysis", "self_reflection", "cross_critique",
        "auto_verify", "completion_check", "mid_analysis",
        "planning_start", "planning_done",
        "synthesis_start", "synthesis_progress", "synthesis_done",
        "xskill_retrieve", "xskill_accumulate",
        "task_update", "agent_status",
        "task_started",
        "debate_turn", "debate_action_phase", "debate_reflection", "debate_gate_check",
        "preparation_started", "preparation_plan", "preparation_complete",
        "report_building", "swarm_activated",
    ):
        return _sse_event({"type": event_type, **{
            k: v for k, v in event.items()
            if k not in ("event_type", "task_id", "timestamp")
        }})

    # Unknown event type — forward raw
    # Frontend needs sequence for dedup/ordering of any new event types
    # added to backend but not yet explicitly handled above.
    return _sse_event({"type": event_type or "unknown", **{
        k: v for k, v in event.items()
        if k not in ("event_type", "task_id", "timestamp")
    }})


async def _persist_user_message(session_id: str, content: str) -> None:
    """Persist user message to DB immediately (crash-safe).

    Uses its own DB session (independent from orchestrator's transaction).
    Best-effort — failures are logged but never bubble up.
    """
    try:
        from app.core.dependencies import _session_factory
        from app.models.database import ConversationMessageModel
        from sqlalchemy import select as _sel, func as _fn
        from datetime import timedelta, timezone as _tz

        if not _session_factory:
            return

        async with _session_factory() as db:
            # Network retries (SSE reconnect) can resend the same message >5s later,
            # causing duplicate user messages in conversation history.
            _cutoff = datetime.now(_tz.utc) - timedelta(seconds=30)
            _dup_result = await db.execute(
                _sel(_fn.count()).select_from(ConversationMessageModel).where(
                    ConversationMessageModel.session_id == session_id,
                    ConversationMessageModel.role == "user",
                    ConversationMessageModel.content == content,
                    ConversationMessageModel.created_at >= _cutoff,
                )
            )
            if (_dup_result.scalar() or 0) > 0:
                return

            db.add(ConversationMessageModel(
                session_id=session_id,
                role="user",
                content=content,
            ))
            await db.commit()
    except Exception as exc:
        logger.warning("user_message_persist_failed", error=str(exc), session_id=session_id)


async def _replay_sse_events(
    conversation_id: str,
    since_sequence: int,
    limit: int = 200,
) -> AsyncGenerator[str, None]:
    """Replay persisted task_events after ``since_sequence`` as SSE strings.

    Lets an SSE client that dropped its connection catch up via Last-Event-ID
    before live streaming resumes. Best-effort: any error yields nothing.

    NOTE: ``sequence`` restarts at 0 for each task, so a bare
    ``sequence > since_sequence`` filter across a session would drop a new
    task's early events. We mirror the WS replay fix (``_replay_missed_events``):
    map the last-seen sequence to its timestamp, then replay by ``created_at``.
    """
    try:
        from app.core.dependencies import _session_factory
        from app.models.database import TaskEventModel

        if not _session_factory:
            return

        async with _session_factory() as db:
            # Resolve the timestamp of the last event the client acknowledged.
            last_seen_at = (
                await db.execute(
                    select(TaskEventModel.created_at)
                    .where(
                        TaskEventModel.session_id == conversation_id,
                        TaskEventModel.sequence == since_sequence,
                    )
                    .order_by(TaskEventModel.created_at.desc())
                    .limit(1)
                )
            ).scalar()

            stmt = select(TaskEventModel).where(
                TaskEventModel.session_id == conversation_id
            )
            if last_seen_at is not None:
                # Replay everything after that point, across any task.
                stmt = stmt.where(TaskEventModel.created_at > last_seen_at)
            stmt = stmt.order_by(
                TaskEventModel.created_at.asc(),
                TaskEventModel.sequence.asc().nullslast(),
                TaskEventModel.id.asc(),
            ).limit(limit)

            result = await db.execute(stmt)
            for row in result.scalars().all():
                payload = dict(row.data or {})
                payload.setdefault("event_type", row.event_type)
                payload["sequence"] = row.sequence
                sse_str = _handle_event(payload)
                if sse_str is not None:
                    if row.sequence and not sse_str.startswith("id:"):
                        sse_str = f"id: {row.sequence}\n{sse_str}"
                    yield sse_str
    except Exception as exc:  # pragma: no cover - best-effort replay
        logger.debug("sse_replay_failed", error=str(exc), conversation_id=conversation_id)


async def _stream_chat_generator(
    message: str,
    user_id: str,
    conversation_id: str | None,
    request: Request,
    model_override: str | None = None,
    use_free: bool = False,
    last_event_id: int = 0,
) -> AsyncGenerator[str, None]:
    """
    Async generator: delegates to TaskRunner and streams events as SSE.

    UNIFIED ARCHITECTURE:
    1. Persists user message immediately
    2. Starts task via TaskRunner (background execution)
    3. Subscribes to task events via queue
    4. Yields events as SSE until task completes or client disconnects
    5. On disconnect: task continues in background, persists when done

    Benefits over old approach:
    - /sessions/{id}/state correctly reports running task
    - Multi-tab users see events via Monitor WS
    - F5 recovery works (task continues, message persisted by TaskRunner)
    - No dual-persist race (TaskRunner is single persist path)
    """
    runner = get_task_runner()

    # Ensure conversation_id is always set
    if not conversation_id:
        conversation_id = str(uuid.uuid4())
        logger.info("sse_generated_conversation_id", conversation_id=conversation_id)

    # (inner finally + outer except), risking stale reference operations.
    # Now: single finally block at the outermost level handles cleanup.
    _subscribed = False
    unsub = None  # type: ignore[assignment]

    try:
        # Send initial heartbeat immediately (Cloudflare 524 timeout protection)
        yield _sse_event({"type": "heartbeat"})

        # Resume: replay events the client missed since its Last-Event-ID.
        # Sequences are per-task; replaying the latest task's tail is enough to
        # catch a reconnecting client up before live streaming resumes.
        if last_event_id > 0:
            async for _replayed in _replay_sse_events(conversation_id, last_event_id):
                yield _replayed

        # Persist user message IMMEDIATELY (before task starts)
        await _persist_user_message(conversation_id, message)

        # Auto-generate title on first message (fire-and-forget)
        try:
            from app.core.conversation_titles import schedule_title_generation
            schedule_title_generation(conversation_id, message)
        except Exception:
            pass

        event_queue, unsub = runner.subscribe_queue(conversation_id)
        _subscribed = True

        # Apply model override if provided by frontend toolbar
        if model_override:
            try:
                model_switcher = get_model_switcher()
                await model_switcher.switch_model(conversation_id, model_override)
            except Exception as exc:
                logger.warning("model_override_failed", model=model_override, error=str(exc))

        # user sends rapid messages via SSE (same protection as WS path).
        mutex = get_session_mutex()
        unlock = await mutex.acquire_with_timeout(conversation_id, timeout=100.0)
        if unlock is None:
            yield _sse_event({"type": "error", "message": "Session busy. Please wait and try again."})
            yield _sse_event({"type": "done", "status": "error", "content": "", "conversation_id": conversation_id})
            return

        try:
            # DUP-TASK-FIX: Check if an identical message was already processed in this session
            # within a recent window. Prevents accidental duplicate task creation.
            try:
                from app.core.dependencies import _session_factory
                if _session_factory:
                    async with _session_factory() as _dup_db:
                        from datetime import timedelta, timezone as _tz2
                        _dup_cutoff = datetime.now(_tz2.utc) - timedelta(minutes=10)
                        _dup_result = await _dup_db.execute(
                            select(TaskModel.id, TaskModel.status)
                            .where(
                                TaskModel.session_id == conversation_id,
                                TaskModel.title == message[:200],
                                TaskModel.created_at >= _dup_cutoff,
                            )
                            .limit(1)
                        )
                        _dup_task = _dup_result.first()
                        if _dup_task and _dup_task.status in ("completed", "running", "pending"):
                            logger.info("duplicate_task_blocked",
                                        session_id=conversation_id,
                                        existing_task_id=str(_dup_task.id),
                                        message_prefix=message[:50])
                            yield _sse_event({
                                "type": "info",
                                "message": "⚠️ This request is identical to one already processed. Skipping duplicate execution.",
                            })
                            yield _sse_event({
                                "type": "done",
                                "status": "duplicate",
                                "content": "",
                                "conversation_id": conversation_id,
                            })
                            return
            except Exception as _dup_err:
                logger.debug("duplicate_check_failed", error=str(_dup_err))

            # Start task via TaskRunner (UNIFIED execution)
            task_id = await runner.start_task(
                conversation_id, user_id, message,
                use_free=use_free
            )
        except RuntimeError as e:
            yield _sse_event({"type": "error", "message": str(e)})
            yield _sse_event({"type": "done", "status": "error", "content": "", "conversation_id": conversation_id})
            return
        finally:
            # Release mutex on ALL exit paths — task itself runs independently.
            unlock()

        # Notify frontend of task start
        yield _sse_event({
            "type": "task_started",
            "task_id": task_id,
            "session_id": conversation_id,
        })

        # BUG-16 FIX: Notify other WS tabs about task_started from SSE path.
        # Without this, WS clients on other tabs never learn a task started via SSE.
        try:
            from app.api.ws import manager as ws_manager
            await ws_manager.send_to_conversation(conversation_id, {
                "type": "session_event",
                "event": "task_started",
                "task_id": task_id,
                "session_id": conversation_id,
            })
        except Exception:
            pass  # Non-critical — don't fail SSE for WS notification failure

        logger.info("sse_task_started", task_id=task_id, conversation_id=conversation_id)

        # Stream events until task completes
        _last_activity = time.monotonic()
        _stale_timeout = _get_stale_timeout()

        while True:
            # Check stale detection (0 = disabled / no stale timeout)
            _idle_time = time.monotonic() - _last_activity
            if _stale_timeout > 0 and _idle_time > _stale_timeout:
                # still actively processing. During long LLM calls (30-120s
                # per agent turn), no events arrive but the task IS alive.
                _task_obj = runner.get_active_task(conversation_id)
                if _task_obj and _task_obj.status == "running":
                    _task_idle = (
                        datetime.now(timezone.utc) - _task_obj.last_activity
                    ).total_seconds()
                    if _task_idle < _stale_timeout:
                        # Task IS making progress — reset and continue
                        _last_activity = time.monotonic()
                        yield _sse_event({
                            "type": "task_heartbeat",
                            "last_activity": _task_obj.last_activity_desc,
                            "current_tool": _task_obj.current_tool,
                        })
                        continue

                # arrived in queue yet.
                if _task_obj is None:
                    _last_task = runner.get_last_task(conversation_id)
                    if _last_task and _last_task.status in ("completed", "failed", "cancelled"):
                        _last_activity = time.monotonic()
                        yield _sse_event({"type": "heartbeat"})
                        continue

                logger.warning("sse_stream_stale", conversation_id=conversation_id,
                               idle_seconds=round(_idle_time, 1))
                yield _sse_event({
                    "type": "info",
                    "message": f"⏳ No activity for {int(_idle_time)}s. Task continues in background.",
                })
                yield _sse_event({
                    "type": "done",
                    "status": "background",
                    "content": "",
                    "conversation_id": conversation_id,
                })
                return

            # Check client disconnect
            if await request.is_disconnected():
                logger.info("sse_client_disconnected", conversation_id=conversation_id)
                return

            # Get next event from queue
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=30.0)
                _last_activity = time.monotonic()
            except asyncio.TimeoutError:
                yield _sse_event({"type": "heartbeat"})
                continue

            event_type = event.get("event_type", "")

            # Handle terminal events
            if event_type == "task_completed":
                final_message = event.get("data", {}).get("message", "")
                if not final_message:
                    final_message = "Task completed but no response generated."

                task_obj = runner.get_last_task(conversation_id)
                tokens_already_streamed = (
                    task_obj.tokens_streamed if task_obj else False
                )

                if not tokens_already_streamed:
                    chunk_size = 12
                    for i in range(0, len(final_message), chunk_size):
                        if await request.is_disconnected():
                            break
                        token = final_message[i:i + chunk_size]
                        yield _sse_event({"type": "token", "content": token})
                        await asyncio.sleep(0.008)

                yield _sse_event({"type": "usage", "usage": {}})
                yield _sse_event({
                    "type": "done",
                    "content": final_message,
                    "conversation_id": conversation_id,
                    "task_id": event.get("task_id"),
                    "agents_spawned": event.get("data", {}).get("agents_spawned", []),
                    "swarm_id": event.get("data", {}).get("swarm_id"),
                    "status": event.get("data", {}).get("status"),
                    "metadata": event.get("data", {}).get("metadata"),
                })
                return

            elif event_type == "task_failed":
                error_msg = event.get("data", {}).get("error", "Task failed")
                yield _sse_event({"type": "error", "message": error_msg})
                yield _sse_event({
                    "type": "done",
                    "status": "error",
                    "content": "",
                    "conversation_id": conversation_id,
                })
                return

            elif event_type == "task_cancelled":
                yield _sse_event({
                    "type": "done",
                    "status": "cancelled",
                    "content": "",
                    "conversation_id": conversation_id,
                })
                return

            else:
                # Regular progress event — convert and yield
                sse_str = _handle_event(event)
                if sse_str is not None:
                    # Prepend an `id:` line (task_events sequence) so a client
                    # that drops the SSE stream can resume via Last-Event-ID.
                    _seq = event.get("sequence")
                    if isinstance(_seq, int) and _seq > 0 and not sse_str.startswith("id:"):
                        sse_str = f"id: {_seq}\n{sse_str}"
                    yield sse_str

    except Exception as e:
        logger.error("sse_stream_error", error=str(e), conversation_id=conversation_id)
        yield _sse_event({"type": "error", "message": str(e)})
        yield _sse_event({
            "type": "done",
            "status": "error",
            "content": "",
            "conversation_id": conversation_id,
        })
    finally:
        if _subscribed and unsub is not None:
            unsub()


def _get_stale_timeout() -> int:
    """Get stale timeout: max seconds without ANY event before declaring stuck."""
    try:
        from app.config import get_settings
        return get_settings().max_stream_duration
    except Exception:
        return 300


# H-03 FIX: In-memory request_id dedup (TTL-based, prevents SSE retry duplicates)
_processed_request_ids: dict[str, float] = {}  # request_id → timestamp
_REQUEST_ID_TTL = 300  # 5 minutes
_MAX_REQUEST_IDS = 10000  # BUG-M9 FIX: Hard cap to prevent unbounded growth


def _check_and_register_request_id(request_id: str | None) -> bool:
    """Return True if this request_id is a duplicate (already processed).

    Also prunes expired entries to prevent memory leak.
    BUG-M9 FIX: Added hard cap at _MAX_REQUEST_IDS to prevent unbounded growth
    in single-worker deployments with high traffic.
    """
    if not request_id:
        return False

    now = time.monotonic()

    # Prune expired entries
    expired = [k for k, ts in _processed_request_ids.items() if now - ts > _REQUEST_ID_TTL]
    for k in expired:
        del _processed_request_ids[k]

    # Hard cap: remove oldest entries if still too large
    if len(_processed_request_ids) > _MAX_REQUEST_IDS:
        sorted_ids = sorted(_processed_request_ids.items(), key=lambda x: x[1])
        for k, _ in sorted_ids[:len(_processed_request_ids) - _MAX_REQUEST_IDS]:
            del _processed_request_ids[k]

    if request_id in _processed_request_ids:
        return True  # Duplicate!

    _processed_request_ids[request_id] = now
    return False


@router.post("/send")
async def send_chat(
    chat_request: ChatRequest,
    user_id: str = Depends(get_optional_user),
) -> dict[str, Any]:
    """
    ARCHITECTURE: DB-first polling, no SSE/WS dependency.

    Fire-and-forget chat endpoint. Starts task in background via TaskRunner
    and returns task_id immediately. Frontend polls GET /tasks/{task_id}/events
    for execution progress.

    This replaces SSE streaming for simpler, more reliable delivery:
    - No connection drops, no 403 errors, no proxy buffering issues
    - Works across tabs, page refreshes, network changes
    - Single source of truth: DB events with sequence numbers
    """
    runner = get_task_runner()

    # Ensure conversation_id
    conversation_id = chat_request.conversation_id
    if not conversation_id:
        conversation_id = str(uuid.uuid4())

    # Persist user message immediately (before task starts)
    await _persist_user_message(conversation_id, chat_request.message)

    # Auto-generate title on first message (fire-and-forget)
    try:
        from app.core.conversation_titles import schedule_title_generation
        schedule_title_generation(conversation_id, chat_request.message)
    except Exception:
        pass

    # Apply model override if provided
    if chat_request.model:
        try:
            model_switcher = get_model_switcher()
            await model_switcher.switch_model(conversation_id, chat_request.model)
        except Exception as exc:
            logger.warning("model_override_failed", model=chat_request.model, error=str(exc))

    # Acquire session mutex (prevent duplicate tasks)
    mutex = get_session_mutex()
    unlock = await mutex.acquire_with_timeout(conversation_id, timeout=100.0)
    if unlock is None:
        raise HTTPException(status_code=409, detail="Session busy. Please wait and try again.")

    try:
        # Start task via TaskRunner (background execution)
        task_id = await runner.start_task(
            conversation_id, user_id, chat_request.message,
            use_free=chat_request.use_free
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    finally:
        unlock()

    # Notify WS clients about task_started (for typing indicators in other tabs)
    try:
        from app.api.ws import manager as ws_manager
        await ws_manager.send_to_conversation(conversation_id, {
            "type": "session_event",
            "event": "task_started",
            "task_id": task_id,
            "session_id": conversation_id,
        })
    except Exception:
        pass

    logger.info("chat_send_task_started", task_id=task_id, conversation_id=conversation_id)

    return {
        "task_id": task_id,
        "session_id": conversation_id,
        "status": "executing",
    }


@router.post("/stream")
async def stream_chat(
    chat_request: ChatRequest,
    request: Request,
    user_id: str = Depends(get_optional_user),
) -> StreamingResponse:
    """
    SSE streaming chat endpoint — UNIFIED via TaskRunner.

    Delegates to TaskRunner which runs CoreOrchestrator in background.
    SSE is just a "live view" that subscribes to task events.

    Event types: step|token|tool_start|tool_done|agent_thinking|event|heartbeat|usage|done|error
    """
    # H-03 FIX: Reject duplicate requests from SSE retry
    if _check_and_register_request_id(chat_request.request_id):
        logger.info("duplicate_request_rejected",
                    request_id=chat_request.request_id,
                    session_id=chat_request.conversation_id)

        async def _dup_generator():
            yield _sse_event({"type": "info", "message": "Request already processed (duplicate detected)."})
            yield _sse_event({"type": "done", "status": "duplicate", "content": "", "conversation_id": chat_request.conversation_id or ""})

        return StreamingResponse(
            _dup_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
        )

    # Resume support: a reconnecting client sends the last SSE id it saw.
    from app.core.sse import parse_last_event_id
    last_event_id = parse_last_event_id(request.headers.get("Last-Event-ID"))

    return StreamingResponse(
        _stream_chat_generator(
            message=chat_request.message,
            user_id=user_id,
            conversation_id=chat_request.conversation_id,
            request=request,
            model_override=chat_request.model,
            use_free=chat_request.use_free,
            last_event_id=last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Encoding": "identity",
            "Vary": "Accept-Encoding",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ============================================================
# ============================================================


class SetProfileRequest(BaseModel):
    """Request body for setting a session profile."""
    profile_name: str = Field(..., min_length=1, max_length=64)


@router.get("/profiles")
async def list_profiles() -> list[dict[str, Any]]:
    """List all available conversation profiles."""
    manager = get_profile_manager()
    return [p.to_dict() for p in manager.list_profiles()]


@router.post("/profiles/{session_id}")
async def set_session_profile(
    session_id: str,
    body: SetProfileRequest,
) -> dict[str, Any]:
    """Apply a conversation profile to a session."""
    manager = get_profile_manager()
    try:
        result = await manager.apply_to_session(session_id, body.profile_name)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/models")
async def list_models() -> list[dict[str, str]]:
    """List all available LLM models."""
    switcher = get_model_switcher()
    return switcher.list_available_models()
