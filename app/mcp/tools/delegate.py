"""
Nexus - Delegate Tool (Hermes-agent inspired).

Allows the core agent to spawn isolated subagents for subtasks.
Each subagent gets its own iteration budget, tool subset, and timeout.

Self-registers with the tool registry at import time.

Lifecycle features:
- Context Injection: subtask receives parent's TASK_BRIEF, TODO, and recent ACTIVITY
- Result Merge: subtask activity is merged back into parent's ACTIVITY.md
- Workspace Cleanup: subtask workspace is removed after merge

Inspired by: hermes-agent/tools/delegate_tool.py
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import shutil
import time
from pathlib import Path
from typing import Any

import structlog

from app.mcp.tool_registry import registry, tool_result, tool_error

logger = structlog.get_logger(__name__)

_MAX_DELEGATION_DEPTH = 3  # Maximum nested delegation depth
# Per-coroutine depth tracking. A module-level int (mutated via `global`) cross-
# contaminates under concurrent delegations: one request's depth leaks into
# another, wrongly tripping the max-depth refusal or corrupting the count when a
# finally-block decrement races a peer. A ContextVar gives each async context its
# own depth — the correct primitive for per-request nesting state.
_current_delegation_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "_current_delegation_depth", default=0
)

# Workspace base (consistent with TaskWorkingMemory)
_WORKSPACE_BASE = Path(os.environ.get("WORKSPACE_DIR", "workspace_data"))

# Side-channel ContextVar: captures subtask_id from orchestrator_fn for merge/cleanup
_last_subtask_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_last_subtask_id", default=""
)

# Context injection limits
_ACTIVITY_TAIL_BYTES = 12288  # Last 12KB of parent's ACTIVITY.md
_SUBTASK_ACTIVITY_MERGE_MAX = 0  # 0 = NO LIMIT — merge FULL subtask activity
_SUMMARY_MERGE_MAX = 0  # 0 = NO LIMIT — merge FULL summary


# ── Context helpers ──────────────────────────────────────────────────────

def _read_parent_context(parent_task_id: str) -> dict[str, str]:
    """Read parent task's context files for injection into subtask.

    Returns dict with keys: task_brief, todo, activity_tail, ledger_summary.
    All values are strings (empty string if file not found).
    """
    result = {"task_brief": "", "todo": "", "activity_tail": "", "ledger_summary": ""}

    if not parent_task_id:
        return result

    from app.core import task_paths
    context_dir = task_paths.context_dir(parent_task_id)

    # Read TASK_BRIEF.md (full)
    try:
        brief_path = context_dir / "TASK_BRIEF.md"
        if brief_path.exists():
            result["task_brief"] = brief_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("delegate_read_parent_brief_error", error=str(exc)[:200])

    # Read TODO.md (full)
    try:
        todo_path = context_dir / "TODO.md"
        if todo_path.exists():
            result["todo"] = todo_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("delegate_read_parent_todo_error", error=str(exc)[:200])

    # Read ACTIVITY.md (last 4KB)
    try:
        activity_path = context_dir / "ACTIVITY.md"
        if activity_path.exists():
            content = activity_path.read_text(encoding="utf-8")
            if len(content) > _ACTIVITY_TAIL_BYTES:
                result["activity_tail"] = content[-_ACTIVITY_TAIL_BYTES:]
            else:
                result["activity_tail"] = content
    except Exception as exc:
        logger.debug("delegate_read_parent_activity_error", error=str(exc)[:200])

    # Read Ledger summary (structured history — prevents subagent from redoing work)
    try:
        from app.orchestration.context_ledger import ContextLedger
        ledger = ContextLedger(parent_task_id)
        entry_count = ledger.get_entry_count()
        if entry_count > 0:
            # Build orchestrator-level summary (richest view) capped at 6KB
            result["ledger_summary"] = ledger.build_context_for_orchestrator(
                current_turn=999999,  # See all turns
                max_chars=6000,
            )
    except Exception as exc:
        logger.debug("delegate_read_parent_ledger_error", error=str(exc)[:200])

    return result


def _build_enriched_context(
    original_context: str,
    parent_context: dict[str, str],
) -> str:
    """Build enriched context by combining parent context with original.

    Includes ledger summary so subagent knows what was already accomplished
    (Recall Before Action — prevents redoing parent's work).
    """
    parts: list[str] = []

    has_parent_context = any(parent_context.values())

    if has_parent_context:
        parts.append("## PARENT TASK CONTEXT\n")

        if parent_context["task_brief"]:
            parts.append("### Task Brief (Goals & Constraints)")
            parts.append(parent_context["task_brief"])
            parts.append("")

        if parent_context["todo"]:
            parts.append("### Current Progress")
            parts.append(parent_context["todo"])
            parts.append("")

        if parent_context.get("ledger_summary"):
            parts.append("### Structured History (DO NOT redo this work)")
            parts.append(parent_context["ledger_summary"])
            parts.append("")

        if parent_context["activity_tail"]:
            parts.append("### Recent Activity (last 4KB)")
            parts.append(parent_context["activity_tail"])
            parts.append("")

        parts.append("---")

    parts.append("## DELEGATED SUBTASK")
    if original_context:
        parts.append(original_context)

    return "\n".join(parts)


def _merge_subtask_result(
    parent_task_id: str,
    subtask_id: str,
    goal: str,
    status: str,
    elapsed: float,
    summary: str,
) -> bool:
    """Merge subtask's FULL activity into parent's ACTIVITY.md.

    DESIGN: Activity is the system's complete record. It must contain
    EVERYTHING — no truncation, no collapsing, no hiding. The parent's
    ACTIVITY.md must be a complete record of all work done including
    all subtask work in full fidelity.

    Subtask content is indented with '> ' blockquote to visually distinguish
    it from parent's own content, but ALL content is preserved inline.
    """
    if not parent_task_id:
        return False

    from app.core import task_paths
    _subtask_ctx = task_paths.context_dir(subtask_id)

    # Read subtask's FULL ACTIVITY.md — no truncation
    subtask_activity = ""
    try:
        subtask_activity_path = _subtask_ctx / "ACTIVITY.md"
        if subtask_activity_path.exists():
            subtask_activity = subtask_activity_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.debug("delegate_read_subtask_activity_error", error=str(exc)[:200])

    # Also read subtask's LEDGER.jsonl for structured data
    subtask_ledger = ""
    try:
        subtask_ledger_path = _subtask_ctx / "LEDGER.jsonl"
        if subtask_ledger_path.exists():
            subtask_ledger = subtask_ledger_path.read_text(encoding="utf-8")
    except Exception:
        pass

    # Also read subtask's TODO.md for progress tracking
    subtask_todo = ""
    try:
        subtask_todo_path = _subtask_ctx / "TODO.md"
        if subtask_todo_path.exists():
            subtask_todo = subtask_todo_path.read_text(encoding="utf-8")
    except Exception:
        pass

    # Blockquote ALL subtask content — preserves full fidelity
    activity_blockquoted = "\n".join(
        f"> {line}" for line in subtask_activity.splitlines()
    ) if subtask_activity.strip() else "> (no activity recorded)"

    ledger_blockquoted = ""
    if subtask_ledger.strip():
        ledger_blockquoted = (
            ">\n> ---\n> **Subtask Ledger (structured conclusions):**\n"
            + "\n".join(f"> {line}" for line in subtask_ledger.splitlines())
        )

    todo_blockquoted = ""
    if subtask_todo.strip():
        todo_blockquoted = (
            ">\n> ---\n> **Subtask TODO (progress tracking):**\n"
            + "\n".join(f"> {line}" for line in subtask_todo.splitlines())
        )

    # Status emoji
    status_emoji = "✅" if status == "completed" else "❌" if status == "failed" else "⏳"

    # Build merge content — FULL inline, no <details> collapse
    merge_content = f"""
## ═══ SUBTASK `{subtask_id[:8]}` | {status_emoji} {status} ═══

**Goal**: {goal}
**Duration**: {elapsed}s
**Summary**: {summary}

### Subtask Full Activity Log

{activity_blockquoted}
{ledger_blockquoted}
{todo_blockquoted}

## ═══ END SUBTASK `{subtask_id[:8]}` ═══

"""

    # Append to parent's ACTIVITY.md
    try:
        parent_activity_path = task_paths.context_file(parent_task_id, "ACTIVITY.md")
        parent_activity_path.parent.mkdir(parents=True, exist_ok=True)

        existing = ""
        if parent_activity_path.exists():
            existing = parent_activity_path.read_text(encoding="utf-8")

        parent_activity_path.write_text(
            existing + "\n" + merge_content,
            encoding="utf-8",
        )
        logger.info(
            "delegate_result_merged",
            parent_task_id=parent_task_id[:8],
            subtask_id=subtask_id[:8],
            merge_size=len(merge_content),
            subtask_activity_size=len(subtask_activity),
        )
        return True

    except Exception as exc:
        logger.warning(
            "delegate_merge_failed",
            parent_task_id=parent_task_id[:8],
            subtask_id=subtask_id[:8],
            error=str(exc)[:300],
        )
        return False


def _merge_subtask_work_files(parent_task_id: str, subtask_id: str) -> int:
    """No-op: subtasks share the parent's _work/ directory.

    Since task_paths.work_dir() routes a subtask's _work to the parent's _work,
    there is nothing to copy — files written by the subtask are already in the
    parent's workspace. (Copying would be a self-copy.) Kept for API stability.
    """
    return 0


def _cleanup_subtask_workspace(subtask_id: str, parent_task_id: str = "") -> None:
    """Remove the subtask's nested directory after its activity is merged.

    A subtask lives at ``tasks/{parent}/subtasks/{child}/`` and holds only its
    own ``_context/`` docs — its ``_work/`` is the parent's shared workspace,
    which lies OUTSIDE this directory and is never touched here. The context
    has already been merged into the parent's ACTIVITY.md, so removing the
    nested dir leaves no orphan.

    Non-fatal: logs warning on failure but never raises.
    """
    if not subtask_id:
        return

    from app.core import task_paths
    # Resolve the subtask's own dir (nested when parent known, else legacy flat).
    if parent_task_id:
        task_paths.register_parent(subtask_id, parent_task_id)
    workspace_path = task_paths.find_task_dir(subtask_id) or task_paths.task_dir(subtask_id)
    try:
        if workspace_path.exists():
            shutil.rmtree(workspace_path)
            logger.info(
                "delegate_workspace_cleaned",
                subtask_id=subtask_id[:8],
                path=str(workspace_path),
            )
    except Exception as exc:
        logger.warning(
            "delegate_workspace_cleanup_failed",
            subtask_id=subtask_id[:8],
            error=str(exc)[:300],
        )


def _check_duplicate_subtask(parent_task_id: str, goal: str) -> str:
    """Check if a similar subtask already exists by scanning the ledger.

    Returns the subtask_id (8-char prefix) if a duplicate is found, else empty string.
    Uses simple keyword overlap to detect semantically similar goals.
    """
    if not parent_task_id:
        return ""

    try:
        from app.orchestration.context_ledger import ContextLedger
        ledger = ContextLedger(parent_task_id)
        entries = ledger.get_all_entries()

        # Extract key tokens from the new goal (lowered, >3 chars)
        goal_tokens = {
            w.lower() for w in goal.split()
            if len(w) > 3 and not w.startswith("--")
        }
        if not goal_tokens:
            return ""

        for entry in entries:
            did_what = entry.get("did_what", "")
            entry_tokens = {
                w.lower() for w in did_what.split()
                if len(w) > 3
            }
            if not entry_tokens:
                continue
            overlap = len(goal_tokens & entry_tokens) / max(len(goal_tokens), 1)
            if overlap > 0.6:
                return entry.get("subtask_id", "") or entry.get("agent_role", "prior_work")

    except Exception as exc:
        logger.debug("dedup_check_error", error=str(exc)[:200])

    return ""


def _salvage_subtask_on_failure(
    parent_task_id: str,
    start_time: float,
    goal: str,
    failure_type: str,
) -> None:
    """Best-effort merge of subtask work when delegation fails (timeout/error).

    Even if the subtask didn't complete, it may have produced useful _work/ files.
    Merge them into the parent and then clean up.
    """
    if not parent_task_id:
        return

    subtask_id = _last_subtask_id.get("") or _find_recent_subtask(parent_task_id, start_time)
    if not subtask_id:
        return

    elapsed = round(time.monotonic() - start_time, 2)
    _merge_subtask_result(
        parent_task_id=parent_task_id,
        subtask_id=subtask_id,
        goal=goal,
        status=f"failed ({failure_type})",
        elapsed=elapsed,
        summary=f"Subtask failed due to {failure_type}. Partial work merged.",
    )
    _cleanup_subtask_workspace(subtask_id, parent_task_id=parent_task_id)
    logger.info(
        "subtask_salvaged_on_failure",
        parent=parent_task_id[:8],
        subtask=subtask_id[:8],
        failure_type=failure_type,
    )


def _find_recent_subtask(parent_task_id: str, start_time: float) -> str:
    """Fallback: find subtask directory created during this delegation window.

    When the ContextVar side-channel fails (e.g. due to context reset),
    we scan the workspace for task directories created after the delegation started.

    Args:
        parent_task_id: The parent task ID.
        start_time: time.monotonic() value when delegation started.
            Converted to wall-clock by subtracting elapsed from current time.
    """
    from app.core import task_paths
    # All descendants — at any delegation depth — live under the ROOT
    # conversation's subtasks/ directory, so scan there (not under the direct
    # parent, which may itself be a nested subtask with no subtasks/ child).
    root_id = task_paths.get_root(parent_task_id)
    subtasks_dir = task_paths.tasks_root() / task_paths.dir_name(root_id) / "subtasks"
    if not subtasks_dir.exists():
        return ""

    # Convert monotonic start_time to approximate wall-clock epoch
    elapsed_since_start = time.monotonic() - start_time
    wall_clock_start = time.time() - elapsed_since_start - 5  # 5s tolerance

    parent_prefix = task_paths.dir_name(parent_task_id)
    candidates: list[tuple[float, str]] = []

    try:
        for entry in subtasks_dir.iterdir():
            if not entry.is_dir():
                continue
            # Don't mistake the parent itself for its newly-spawned subtask.
            if entry.name == parent_prefix:
                continue
            # Check creation time (use TASK_BRIEF as proxy — it's created first)
            brief = entry / "_context" / "TASK_BRIEF.md"
            if brief.exists():
                mtime = brief.stat().st_mtime
                if mtime >= wall_clock_start:
                    candidates.append((mtime, entry.name))
    except Exception as exc:
        logger.debug("find_recent_subtask_scan_error", error=str(exc)[:200])
        return ""

    if not candidates:
        return ""

    # Return the most recently created one
    candidates.sort(key=lambda x: x[0], reverse=True)
    found = candidates[0][1]
    logger.info(
        "subtask_id_recovered_via_fallback",
        parent=parent_prefix,
        subtask=found,
    )
    return found


# ── Orchestrator function for SubagentDelegator ──────────────────────────

async def _create_orchestrator_fn(
    message: str,
    context: str | None = None,
    is_subagent: bool = True,
    max_iterations: int = 50,
    **kwargs: Any,
) -> dict[str, Any]:
    """Bridge function that runs a subtask through CoreOrchestrator.

    This is the ``orchestrator_fn`` that SubagentDelegator calls internally.
    Each invocation gets a fresh DB session and orchestrator instance.
    """
    from app.core.dependencies import _session_factory
    from app.core.execution_context import (
        current_task_id, current_session_id, current_parent_task_id,
    )
    from app.services.orchestrator import CoreOrchestrator

    if _session_factory is None:
        return {
            "success": False,
            "response": "Database not initialized",
            "error": "No DB session factory",
            "subtask_id": "",
        }

    # Save parent context BEFORE subtask clears it
    _parent_task_id = current_task_id.get("")
    _parent_session_id = current_session_id.get("")

    # Set parent_task_id so subtask's workspace resolves to PARENT's _work/
    # This ensures all agents operate on the same codebase — no separate workspaces.
    if _parent_task_id:
        current_parent_task_id.set(_parent_task_id)

    subtask_task_id = ""
    try:
        full_message = message
        if context:
            full_message = f"Context:\n{context}\n\nTask:\n{message}"

        async with _session_factory() as db:
            orchestrator = CoreOrchestrator(db)
            # Use unique session_id for subtask to avoid polluting parent's
            # conversation history with subtask messages
            import uuid as _uuid
            _subtask_session_id = f"{_parent_session_id}:sub:{_uuid.uuid4().hex[:8]}" if _parent_session_id else None
            result = await orchestrator.process_user_request(
                message=full_message,
                user_id="subagent",
                conversation_id=_subtask_session_id,
            )
            await db.commit()

            # Capture subtask_id IMMEDIATELY — before context reset clears ContextVar.
            # Primary source: orchestrator._last_task_id (set inside process_user_request,
            # survives reset_execution_context in finally block).
            subtask_task_id = (
                getattr(orchestrator, '_last_task_id', '')
                or current_task_id.get("")
            )

            # Record the parent link so post-run merge/cleanup resolve the
            # subtask's nested directory even if the cache was cold.
            if subtask_task_id and _parent_task_id and subtask_task_id != _parent_task_id:
                from app.core import task_paths
                task_paths.register_parent(subtask_task_id, _parent_task_id)

            response_text = ""
            subtask_status = ""
            if isinstance(result, dict):
                response_text = result.get("message", str(result))
                # The real outcome lives in metadata.status ("complete" on genuine success);
                # the top-level status is always "completed" (= "the run finished"), so it
                # cannot distinguish a successful task from a failed one. Mirror the actual
                # state so the parent agent doesn't treat a failed subtask as done.
                subtask_status = (result.get("metadata") or {}).get("status", "")
            else:
                response_text = str(result) if result else ""

            return {
                "success": subtask_status == "complete",
                "status": subtask_status or "unknown",
                "response": response_text,
                "api_calls": result.get("metadata", {}).get("api_calls", 0) if isinstance(result, dict) else 0,
                "subtask_id": subtask_task_id,
            }

    except Exception as exc:
        logger.error("subagent_orchestrator_fn_error", error=str(exc)[:300])
        # Capture subtask_id even on failure for cleanup
        subtask_task_id = subtask_task_id or current_task_id.get("")
        return {
            "success": False,
            "response": "",
            "error": str(exc),
            "subtask_id": subtask_task_id,
        }

    finally:
        # ALWAYS store subtask_id in side-channel — this is the critical bridge
        # between _create_orchestrator_fn and _delegate_task's cleanup logic.
        if subtask_task_id and subtask_task_id != _parent_task_id:
            _last_subtask_id.set(subtask_task_id)

        # Release subtask's working memory lock to prevent accumulation
        if subtask_task_id:
            from app.memory.task_working_memory import _cleanup_task_lock
            _cleanup_task_lock(subtask_task_id)

        # RESTORE parent context
        if _parent_task_id:
            current_task_id.set(_parent_task_id)
        if _parent_session_id:
            current_session_id.set(_parent_session_id)
        current_parent_task_id.set("")


# ── Tool handler ─────────────────────────────────────────────────────────

async def _delegate_task(args: dict[str, Any]) -> str:
    """Spawn a subagent to handle an isolated subtask."""
    task = args.get("task", "").strip()
    if not task:
        return tool_error("Task description is required")

    depth = _current_delegation_depth.get(0)
    if depth >= _MAX_DELEGATION_DEPTH:
        return tool_error(
            f"Maximum delegation depth ({_MAX_DELEGATION_DEPTH}) reached. "
            "Complete the task directly instead of delegating further."
        )

    max_iterations = args.get("max_iterations", 15)
    timeout = args.get("timeout", 8000)
    context = args.get("context", "")

    # ── Part A: Context Injection ──
    # Read parent task context for injection into subtask
    from app.core.execution_context import current_task_id
    parent_task_id = current_task_id.get("")

    # ── Part A.0: Deduplication Check ──
    # Prevent spawning duplicate subtasks for the same goal
    duplicate_id = _check_duplicate_subtask(parent_task_id, task)
    if duplicate_id:
        logger.info(
            "delegate_duplicate_skipped",
            parent=parent_task_id[:8] if parent_task_id else "none",
            duplicate=duplicate_id,
            goal=task[:80],
        )
        return tool_result(
            success=True,
            output=(
                f"⚠️ A similar subtask already exists/completed (ID: {duplicate_id}). "
                f"Skipping duplicate delegation. Use the existing results instead."
            ),
            data={"duplicate_of": duplicate_id, "task": task[:200]},
        )

    parent_context = _read_parent_context(parent_task_id)
    enriched_context = _build_enriched_context(context, parent_context)

    # Log context handoff to ACTIVITY.md
    if parent_task_id and any(parent_context.values()):
        try:
            from app.orchestration.activity_logger import ActivityLogger
            _al = ActivityLogger(parent_task_id)
            context_sizes = {
                k: len(v.encode("utf-8")) for k, v in parent_context.items() if v
            }
            asyncio.get_event_loop().create_task(
                _al.log_context_handoff(
                    direction="SENT_TO_SUBTASK",
                    counterpart_task_id=f"(pending)",
                    context_keys=[k for k, v in parent_context.items() if v],
                    context_sizes=context_sizes,
                    goal=task[:300],
                )
            )
        except Exception:
            pass

    logger.info(
        "delegate_task_start",
        task_preview=task[:100],
        max_iterations=max_iterations,
        timeout=timeout,
        parent_task_id=parent_task_id[:8] if parent_task_id else "none",
        context_injected=any(parent_context.values()),
    )

    _current_delegation_depth.set(depth + 1)
    start_time = time.monotonic()
    subtask_id = ""

    try:
        from app.services.subagent_delegator import SubagentDelegator

        delegator = SubagentDelegator(
            orchestrator_fn=_create_orchestrator_fn,
            max_iterations=max_iterations,
        )

        delegation_result = await asyncio.wait_for(
            delegator.delegate_single(
                goal=task,
                context=enriched_context or None,
            ),
            timeout=timeout,
        )

        elapsed = round(time.monotonic() - start_time, 2)
        content = delegation_result.summary or delegation_result.error or ""
        success = delegation_result.status == "completed"

        # ── Part B: Result Merge ──
        subtask_id = _last_subtask_id.get("")
        if not subtask_id and parent_task_id:
            subtask_id = _find_recent_subtask(parent_task_id, start_time)

        if subtask_id and parent_task_id:
            _merge_subtask_result(
                parent_task_id=parent_task_id,
                subtask_id=subtask_id,
                goal=task,
                status=delegation_result.status,
                elapsed=elapsed,
                summary=content,
            )

            try:
                from app.orchestration.activity_logger import ActivityLogger
                _al = ActivityLogger(parent_task_id)
                asyncio.get_event_loop().create_task(
                    _al.log_context_handoff(
                        direction="RECEIVED_FROM_SUBTASK",
                        counterpart_task_id=subtask_id,
                        context_keys=["activity", "ledger", "todo", "work_files"],
                        context_sizes={
                            "summary": len((delegation_result.summary or "").encode("utf-8")),
                        },
                        goal=task[:300],
                    )
                )
            except Exception:
                pass

        logger.info(
            "delegate_task_complete",
            task_preview=task[:60],
            success=success,
            status=delegation_result.status,
            content_length=len(content),
            duration=delegation_result.duration_seconds,
            subtask_id=subtask_id[:8] if subtask_id else "unknown",
        )

        return tool_result(
            success=success,
            output=f"🤖 **Subagent result** (status: {delegation_result.status}):\n\n{content[:20000]}",
            data={
                "task": task[:200],
                "status": delegation_result.status,
                "content": content[:10000],
                "duration_seconds": delegation_result.duration_seconds,
                "subtask_id": subtask_id[:8] if subtask_id else "",
            },
        )

    except asyncio.TimeoutError:
        logger.warning("delegate_task_timeout", task=task[:60], timeout=timeout)
        _salvage_subtask_on_failure(parent_task_id, start_time, task, "timeout")
        return tool_error(f"Subagent timed out after {timeout}s. Task: {task[:100]}")

    except ImportError as ie:
        logger.warning("delegate_not_available", error=str(ie))
        return tool_error(
            "Delegation not available. The subagent delegator module is not installed. "
            "Please handle this task directly."
        )

    except Exception as e:
        logger.error("delegate_task_error", error=str(e), task=task[:60])
        _salvage_subtask_on_failure(parent_task_id, start_time, task, "error")
        return tool_error(f"Delegation failed: {e}")

    finally:
        _current_delegation_depth.set(max(0, _current_delegation_depth.get(0) - 1))
        # ALWAYS cleanup subtask workspace — regardless of success/failure/timeout.
        # The side-channel or fallback scan finds the subtask_id; if found, remove it.
        # This prevents orphan task directories from accumulating.
        _cleanup_id = subtask_id or _last_subtask_id.get("")
        if not _cleanup_id and parent_task_id:
            _cleanup_id = _find_recent_subtask(parent_task_id, start_time)
        if _cleanup_id:
            _cleanup_subtask_workspace(_cleanup_id, parent_task_id=parent_task_id)


# ── Self-registration ────────────────────────────────────────────────────

registry.register(
    name="delegate_task",
    toolset="delegation",
    schema={
        "description": (
            "Delegate a subtask to an isolated subagent. "
            "Use for tasks that need focused execution, parallel work, "
            "or when the current context is too large. "
            "The subagent gets its own clean context and iteration budget. "
            "Parent task brief and activity will be auto-injected for context continuity."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Clear description of the subtask to delegate",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "CRITICAL: Provide comprehensive context including: file paths involved, "
                        "error messages, project structure, constraints, and any relevant findings. "
                        "The more context you provide, the better the subtask can complete. "
                        "Parent task brief and activity will be auto-injected."
                    ),
                },
                "max_iterations": {
                    "type": "integer",
                    "description": "Maximum tool call iterations for the subagent (default: 15)",
                    "default": 15,
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds for the entire delegation (default: 2000)",
                    "default": 2000,
                },
            },
            "required": ["task"],
        },
    },
    handler=_delegate_task,
    is_async=True,
    emoji="🤖",
)
