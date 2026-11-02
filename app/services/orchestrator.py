"""
Nexus - Core Orchestrator (Refactored).

Thin wrapper that handles:
- Session management
- Execution context setup
- Slash command interception
- Memory enrichment (KG, active recall, XSkill, memory layers)
- Delegates actual execution to NexusExecutionPipeline
"""

import asyncio
import datetime
import os
import uuid
from typing import Any, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.action_logger import get_action_logger
from app.core.exceptions import NexusError
from app.core.session_manager import SessionManager, get_session_manager, make_session_key
from app.mcp.tool_executor import ToolExecutor
from app.models.database import TaskModel
from app.services.llm_client import get_core_llm_with_fallback

logger = structlog.get_logger(__name__)

# GAP-6 FIX: Cross-task learning within same session.
# Stores compact summaries of completed tasks per conversation_id.
# When a new task starts in the same conversation, prior summaries are
# injected into memory_context so the new task knows what was already done.
_session_task_summaries: dict[str, list[str]] = {}  # conversation_id → [summary, ...]
_SESSION_SUMMARY_MAX_PER_CONVERSATION = 5
_SESSION_SUMMARY_MAX_CHARS = 1500


class CoreOrchestrator:
    """
    Core Orchestrator — Entry point for user requests.

    Simplified architecture:
    1. Setup execution context (session, cancellation, file tracking)
    2. Handle slash commands (/think, /model, /profile)
    3. Enrich context (memory, KG, XSkill)
    4. Delegate to NexusExecutionPipeline (unified turn-based system)
    5. Persist results and clean up
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.llm = get_core_llm_with_fallback()
        self.tool_executor = ToolExecutor()
        self.action_logger = get_action_logger()

        # Optional subsystems (initialized lazily, failures are non-critical)
        self._profile_manager = None
        self._reasoning_ctrl = None

        try:
            from app.core.conversation_profiles import get_profile_manager
            self._profile_manager = get_profile_manager()
        except Exception:
            pass

        try:
            from app.core.reasoning_control import get_reasoning_controller
            self._reasoning_ctrl = get_reasoning_controller()
        except Exception:
            pass

        logger.info("orchestrator_init_complete", mode="nexus_pipeline")

    async def process_user_request(
        self,
        message: str,
        user_id: str = "default_user",
        conversation_id: Optional[str] = None,
        event_callback: Optional[Any] = None,
        use_free: bool = False,
        resume_checkpoint: Optional[dict] = None,
    ) -> dict[str, Any]:
        """
        Process user request — entry point.

        This method maintains the same interface as before for compatibility
        with TaskRunner and API endpoints.

        Args:
            resume_checkpoint: If provided, pipeline resumes from this checkpoint.
        """
        conversation_id = conversation_id or str(uuid.uuid4())

        # ── Long message chunked processing ──
        from app.config import get_settings
        from app.services.long_message_processor import LongMessageProcessor

        _lm_settings = get_settings()
        _lm_processor = LongMessageProcessor(settings=_lm_settings, llm_client=self.llm)
        if _lm_processor.needs_chunking(message):
            _lm_result = await _lm_processor.process(message, session_id=conversation_id)
            logger.info(
                "long_message_processed",
                original_length=_lm_result.original_length,
                processed_length=len(_lm_result.processed_message),
                strategy=_lm_result.strategy_used,
                chunks=_lm_result.chunks_processed,
            )
            message = _lm_result.processed_message

        # ── Set execution context ──
        from app.core.execution_context import (
            current_session_id,
            current_event_callback,
            current_task_id,
            current_file_tracker,
            reset_execution_context,
        )
        current_session_id.set(conversation_id)
        current_event_callback.set(event_callback)

        # Switch to free LLM if requested (with fallback chain for resilience)
        if use_free:
            from app.services.llm_client import get_core_llm_with_kilo_fallback
            self.llm = get_core_llm_with_kilo_fallback()
            logger.info("using_free_llm_with_fallback")

        _ext_task_id = getattr(self, '_external_task_id', None)
        if _ext_task_id:
            current_task_id.set(str(_ext_task_id))

        # ── Initialize FileContextTracker ──
        try:
            from app.core.file_context_tracker import FileContextTracker
            from app.mcp.tools.filesystem import get_task_workspace
            _task_id = current_task_id.get("")
            _ws_path = get_task_workspace()
            _file_tracker = FileContextTracker(task_id=_task_id or conversation_id, workspace_path=_ws_path)
            current_file_tracker.set(_file_tracker)
        except Exception:
            pass

        # ── Session management ──
        _session_mgr = get_session_manager()
        _session_key = make_session_key(user_id, conversation_id)
        _session_acquired = False
        try:
            await _session_mgr.get_or_create_session(user_id, conversation_id)
            await _session_mgr.acquire_session(_session_key)
            _session_acquired = True
        except Exception as _acq_err:
            logger.warning("session_acquire_failed", error=str(_acq_err))

        # ── Log user input ──
        self.action_logger.log_user_input(
            session_id=conversation_id,
            content=message,
            user_id=user_id,
        )

        try:
            # ── Slash commands ──
            slash_result = await self._handle_slash_commands(message, conversation_id)
            if slash_result is not None:
                return {
                    "conversation_id": conversation_id,
                    "message": slash_result,
                    "status": "completed",
                    "task_id": None,
                    "agents_spawned": [],
                    "swarm_id": None,
                }

            # ── Memory enrichment ──
            memory_context = await self._enrich_context(message, conversation_id, user_id)

            # GAP-6 FIX: Inject prior task summaries from same conversation
            prior_summaries = _session_task_summaries.get(conversation_id, [])
            if prior_summaries:
                prior_text = "\n".join(
                    f"- Task {i+1}: {s}" for i, s in enumerate(prior_summaries)
                )
                memory_context = (
                    f"## Prior Tasks in This Session (use as context, do NOT repeat)\n"
                    f"{prior_text}\n\n{memory_context}"
                )

            # ── Get or create task ID ──
            # Capture parent_task_id BEFORE overwriting (set when called from delegate)
            parent_task_id = current_task_id.get("")
            # Resolution order:
            #   1. Explicit external id (TaskRunner/API supplied) → honor it.
            #   2. Top-level message in a conversation that already has a task
            #      → reuse it so one conversation maps to ONE task folder.
            #   3. Otherwise → fresh uuid.
            # Subtasks (parent_task_id set by delegate) ALWAYS get a fresh id and
            # are nested under their parent — they must never reuse the parent's.
            if _ext_task_id:
                task_id = str(_ext_task_id)
            elif not parent_task_id and (
                reused := await self._find_reusable_task(conversation_id)
            ):
                task_id = reused
                logger.info(
                    "task_reused_for_conversation",
                    conversation_id=conversation_id,
                    task_id=task_id[:8],
                )
            else:
                task_id = str(uuid.uuid4())
            current_task_id.set(task_id)
            # Store on instance so callers (delegate tool) can retrieve after context reset
            self._last_task_id = task_id

            # ── Persist user message (idempotent) ──
            await self._persist_user_message(message, conversation_id)

            # ── Write task meta ──
            self._write_task_meta(task_id, conversation_id, message, parent_task_id=parent_task_id)

            # ── Ensure task record exists in DB (required for FK refs from checkpoints/skills) ──
            await self._ensure_task_in_db(task_id, conversation_id, user_id, message, parent_task_id)

            # ── Build agent catalog ──
            from app.orchestration.pipeline import build_agent_catalog_text
            agent_catalog = build_agent_catalog_text()

            # ── Get tool schemas ──
            tool_schemas = ToolExecutor.get_tool_schemas()

            # ── Cancellation ──
            from app.core.cancellation import register_token, unregister_token
            cancel_token = register_token(task_id)

            # ── Emit task started ──
            if event_callback:
                try:
                    await event_callback({
                        "event_type": "task_started",
                        "task_id": task_id,
                        "title": message[:100],
                    })
                except Exception:
                    pass

            # ═══════════════════════════════════════════════════════════
            # DELEGATE TO NEXUS EXECUTION PIPELINE
            # ═══════════════════════════════════════════════════════════
            from app.orchestration.pipeline import NexusExecutionPipeline

            pipeline = NexusExecutionPipeline(
                llm=self.llm,
                tool_executor=self.tool_executor,
                tool_schemas=tool_schemas,
                event_callback=event_callback,
                cancel_check=lambda: cancel_token.is_cancelled,
                task_id=task_id,
                db=self.db,
            )

            pipeline_result = await pipeline.run(
                message=message,
                memory_context=memory_context,
                agent_catalog=agent_catalog,
                resume_from_checkpoint=resume_checkpoint,
            )

            # ── Store task result in DB (status updated by TaskRunner AFTER
            # assistant message persist to avoid race condition) ──
            try:
                # Recover session if a prior flush poisoned it
                if not self.db.is_active:
                    await self.db.rollback()

                from sqlalchemy import select
                result = await self.db.execute(
                    select(TaskModel).where(TaskModel.id == uuid.UUID(task_id))
                )
                task = result.scalar_one_or_none()
                if task:
                    task.result = {
                        "content": pipeline_result["content"][:50000],
                        "content_length": len(pipeline_result["content"]),
                    }
                    await self.db.flush()
                    await self.db.commit()
            except Exception as _db_err:
                logger.debug("task_result_update_failed", error=str(_db_err))
                try:
                    await self.db.rollback()
                except Exception:
                    pass

            # NOTE: Do NOT emit task_update "completed" here.
            # TaskRunner emits task_completed event AFTER persisting assistant message.
            # Emitting here causes frontend to see "completed" before message is available.

            # GAP-6 FIX: Store compact summary for cross-task learning
            _result_content = pipeline_result.get("content", "")
            if _result_content and pipeline_result.get("metadata", {}).get("status") == "complete":
                _summary = f"{message[:100]} → {_result_content[:_SESSION_SUMMARY_MAX_CHARS]}"
                _summaries = _session_task_summaries.setdefault(conversation_id, [])
                _summaries.append(_summary)
                if len(_summaries) > _SESSION_SUMMARY_MAX_PER_CONVERSATION:
                    _session_task_summaries[conversation_id] = _summaries[-_SESSION_SUMMARY_MAX_PER_CONVERSATION:]

            # ── Unregister cancellation ──
            unregister_token(task_id)

            # ── Log response ──
            self.action_logger.log_agent_response(
                session_id=conversation_id,
                agent_name="Nexus Orchestrator",
                content=pipeline_result["content"],
            )

            return {
                "conversation_id": conversation_id,
                "message": pipeline_result["content"],
                "status": "completed",
                "task_id": pipeline_result.get("task_id"),
                "agents_spawned": [],
                "swarm_id": None,
                "metadata": pipeline_result.get("metadata"),
                "tokens_streamed": pipeline_result.get("tokens_streamed", False),
            }

        finally:
            reset_execution_context()
            if _session_acquired:
                await _session_mgr.release_session(_session_key)

    # ════════════════════════════════════════════════════════════════
    # Helper Methods
    # ════════════════════════════════════════════════════════════════

    async def _handle_slash_commands(self, message: str, conversation_id: str) -> Optional[str]:
        """Handle /think, /model, /profile slash commands."""
        if not message.startswith("/"):
            return None

        parts = message.strip().split(None, 1)
        command = parts[0].lower()

        if command == "/think" and self._reasoning_ctrl:
            level = parts[1].strip() if len(parts) > 1 else "high"
            try:
                self._reasoning_ctrl.set_session_level(conversation_id, level)
                return f"✅ Reasoning level set to: **{level}**"
            except Exception as e:
                return f"❌ Failed to set reasoning level: {e}"

        if command == "/profile" and self._profile_manager:
            profile_name = parts[1].strip() if len(parts) > 1 else ""
            if not profile_name:
                profiles = self._profile_manager.list_profiles()
                return "Available profiles: " + ", ".join(profiles)
            try:
                self._profile_manager.set_session_profile(conversation_id, profile_name)
                return f"✅ Profile switched to: **{profile_name}**"
            except Exception as e:
                return f"❌ Failed to switch profile: {e}"

        if command == "/model":
            try:
                from app.core.model_switcher import get_model_switcher
                switcher = get_model_switcher()
                model_name = parts[1].strip() if len(parts) > 1 else ""
                if not model_name:
                    current = switcher.get_current_model(conversation_id)
                    return f"Current model: **{current}**"
                switcher.set_session_model(conversation_id, model_name)
                return f"✅ Model switched to: **{model_name}**"
            except Exception as e:
                return f"❌ Failed to switch model: {e}"

        return None  # Not a recognized slash command

    async def _enrich_context(
        self,
        message: str,
        conversation_id: str,
        user_id: str,
    ) -> str:
        """Gather memory context from all available systems using parallel aggregation.

        Uses SmartContextAggregator for parallel fetch, relevance scoring,
        deduplication, and budget enforcement.
        """
        from app.core.smart_context import SmartContextAggregator
        from app.config import get_settings

        settings = get_settings()

        aggregator = SmartContextAggregator(
            budget_chars=self._get_context_budget(),
            dedup_threshold=0.6,
        )
        timeout = settings.context_source_timeout

        # Register all context sources with their fetchers
        aggregator.add_source(
            "knowledge_graph",
            lambda q, **kw: self._fetch_knowledge_graph(q),
            timeout=timeout,
        )
        aggregator.add_source(
            "active_recall",
            lambda q, **kw: self._fetch_active_recall(q),
            timeout=timeout,
        )
        aggregator.add_source(
            "memory_layers",
            lambda q, **kw: self._fetch_memory_layers(q, conversation_id, user_id),
            timeout=timeout,
        )
        aggregator.add_source(
            "xskill",
            lambda q, **kw: self._fetch_xskill(q),
            timeout=timeout,
        )
        aggregator.add_source(
            "rag",
            lambda q, **kw: self._fetch_rag(q),
            timeout=timeout,
        )
        aggregator.add_source(
            "history",
            lambda q, **kw: self._fetch_history(q, conversation_id),
            timeout=timeout,
        )

        return await aggregator.aggregate(user_query=message)

    def _get_context_budget(self) -> int:
        """Adaptive budget based on task complexity."""
        from app.config import get_settings

        settings = get_settings()
        if getattr(self, "_is_complex_task", False):
            return settings.context_budget_complex
        return settings.context_budget_simple

    # ────────────────────────────────────────────────────────────────
    # Individual context source fetchers (used by SmartContextAggregator)
    # ────────────────────────────────────────────────────────────────

    async def _fetch_knowledge_graph(self, message: str) -> Optional[str]:
        """Fetch knowledge graph context."""
        from app.core.dependencies import _session_factory
        from app.memory.manager import MemoryManager

        if not _session_factory:
            return None

        async with _session_factory() as db:
            kg_mm = MemoryManager(db)
            kg_results = await kg_mm.search_knowledge(
                query=message[:500],
                limit=5,
                include_relations=True,
                max_hops=1,
            )
            if kg_results:
                lines = ["## Prior Knowledge\n"]
                for node in kg_results:
                    desc = node.get("description", "")
                    if desc and len(desc) > 20:
                        name = node.get("name", "")
                        lines.append(f"- **{name}**: {desc[:300]}")
                if len(lines) > 1:
                    return "\n".join(lines)
        return None

    async def _fetch_active_recall(self, message: str) -> Optional[str]:
        """Fetch active memory recall context."""
        from app.core.dependencies import _session_factory
        from app.memory.active_recall import ActiveMemoryRecall
        from app.memory.manager import MemoryManager as RecallMM

        if not _session_factory:
            return None

        async with _session_factory() as db:
            recall_mm = RecallMM(db)
            recall = ActiveMemoryRecall(
                memory_manager=recall_mm,
                llm_client=self.llm,
                max_results=5,
                min_relevance=0.3,
                max_context_chars=2000,
            )
            recall_result = await recall.search(current_message=message, session_messages=[])
            if recall_result.has_memories:
                return recall_result.prompt_injection
        return None

    async def _fetch_memory_layers(
        self, message: str, conversation_id: str, user_id: str
    ) -> Optional[str]:
        """Fetch memory layers context."""
        from app.core.dependencies import _session_factory
        from app.memory.layers.bridge import get_memory_layers_bridge

        if not _session_factory:
            return None

        mem_bridge = get_memory_layers_bridge()
        if mem_bridge:
            async with _session_factory() as db:
                ml_context = await mem_bridge.get_recall_context(
                    db,
                    query=message,
                    session_id=conversation_id,
                    agent_id="orchestrator",
                    user_id=user_id,
                )
                if ml_context:
                    return ml_context
        return None

    async def _fetch_xskill(self, message: str) -> Optional[str]:
        """Fetch XSkill prior knowledge."""
        from app.core.dependencies import _session_factory
        from app.xskill.engine import ContinualLearningEngine

        if not _session_factory:
            return None

        async with _session_factory() as db:
            xskill = ContinualLearningEngine(db, self.llm)
            knowledge = await xskill.retrieve_knowledge(message)
            xskill_ctx = knowledge.get("context", "")
            if xskill_ctx:
                return xskill_ctx[:3000]
        return None

    async def _fetch_rag(self, message: str) -> Optional[str]:
        """Fetch RAG hybrid retrieval context."""
        from app.core.dependencies import _session_factory
        from app.rag.retriever import HybridRetriever, format_retrieval_context

        if not _session_factory:
            return None

        async with _session_factory() as db:
            retriever = HybridRetriever(db)
            rag_results = await retriever.retrieve(query=message[:500], top_k=5)
            if rag_results:
                rag_context = format_retrieval_context(rag_results, max_chars=4000)
                if rag_context:
                    return f"## RAG Knowledge\n\n{rag_context}"
        return None

    async def _fetch_history(self, message: str, conversation_id: str) -> Optional[str]:
        """Fetch recent conversation history."""
        from app.core.dependencies import _session_factory
        from app.memory.conversation_context import load_conversation_history

        if not _session_factory:
            return None

        async with _session_factory() as db:
            history = await load_conversation_history(
                db,
                conversation_id,
                exclude_current=True,
                current_message_content=message,
            )
            if history:
                recent = history[-5:]
                hist_lines = ["## Recent Conversation\n"]
                for msg in recent:
                    role = msg.get("role", "")
                    content = msg.get("content", "")[:300]
                    hist_lines.append(f"**{role}**: {content}")
                return "\n".join(hist_lines)
        return None

    async def _persist_user_message(self, message: str, conversation_id: str) -> None:
        """Persist user message to DB (idempotent)."""
        try:
            from app.models.database import ConversationMessageModel
            from sqlalchemy import select, func
            from datetime import timedelta

            cutoff = datetime.datetime.now(datetime.timezone.utc) - timedelta(seconds=30)
            existing = await self.db.execute(
                select(func.count()).select_from(ConversationMessageModel).where(
                    ConversationMessageModel.session_id == conversation_id,
                    ConversationMessageModel.role == "user",
                    ConversationMessageModel.content == message,
                    ConversationMessageModel.created_at >= cutoff,
                )
            )
            if (existing.scalar() or 0) == 0:
                async with self.db.begin_nested():
                    self.db.add(ConversationMessageModel(
                        session_id=conversation_id,
                        role="user",
                        content=message,
                    ))
                    await self.db.flush()
        except Exception:
            pass  # Best-effort

    async def _find_reusable_task(self, conversation_id: str) -> Optional[str]:
        """Find an existing top-level task for this conversation to reuse.

        Implements "1 conversation = 1 task folder": the most recent top-level
        (parent_task_id IS NULL) task in a non-terminal state for this session
        is reused so follow-up messages become new turns in the SAME folder
        rather than spawning sibling folders.

        Returns the task id (str) or None if no reusable task exists.
        """
        if not conversation_id:
            return None
        try:
            from sqlalchemy import select
            result = await self.db.execute(
                select(TaskModel.id)
                .where(
                    TaskModel.session_id == conversation_id,
                    TaskModel.parent_task_id.is_(None),
                    TaskModel.status.in_(
                        ("pending", "decomposing", "planning", "executing")
                    ),
                )
                .order_by(TaskModel.created_at.desc())
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            return str(existing) if existing else None
        except Exception as exc:
            logger.debug("find_reusable_task_failed", error=str(exc)[:200])
            return None

    async def _ensure_task_in_db(
        self, task_id: str, conversation_id: str, user_id: str, message: str, parent_task_id: str = ""
    ) -> None:
        """Ensure TaskModel exists in DB so FK references (checkpoints, skills) don't fail."""
        try:
            from sqlalchemy import select
            result = await self.db.execute(
                select(TaskModel.id).where(TaskModel.id == uuid.UUID(task_id)).limit(1)
            )
            if result.scalar() is not None:
                return

            parent_uuid = None
            if parent_task_id:
                try:
                    parent_uuid = uuid.UUID(parent_task_id)
                except ValueError:
                    pass

            task = TaskModel(
                id=uuid.UUID(task_id),
                title=self._extract_task_title(message),
                status="executing",
                user_id=user_id,
                session_id=conversation_id,
                parent_task_id=parent_uuid,
            )
            self.db.add(task)
            await self.db.flush()
        except Exception as e:
            logger.debug("ensure_task_in_db_failed", error=str(e), task_id=task_id[:8])

    @staticmethod
    def _extract_task_title(message: str) -> str:
        """Extract meaningful title from task message, skipping Context: prefix."""
        task_marker = "\nTask:\n"
        if task_marker in message:
            task_part = message.split(task_marker, 1)[1]
            return task_part[:100].strip()
        if message.startswith("Context:\n"):
            lines = message.split("\n")
            for line in lines[1:]:
                stripped = line.strip()
                if stripped and not stripped.startswith("Context"):
                    return stripped[:100]
        return message[:100]

    def _write_task_meta(
        self, task_id: str, conversation_id: str, message: str, parent_task_id: str = ""
    ) -> None:
        """Write .task_meta.json for filesystem self-description.

        Also registers the task→parent relationship with the path resolver so
        subtask directories nest correctly. Failures are logged (not silently
        swallowed) — this file is the restart-safe source for parent resolution.
        """
        try:
            import json
            from app.core import task_paths

            # Register relationship FIRST so task_dir() nests subtasks correctly.
            task_paths.register_parent(task_id, parent_task_id or None)

            meta: dict[str, Any] = {
                "task_id": task_id,
                "session_id": conversation_id,
                "title": self._extract_task_title(message),
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            if parent_task_id:
                meta["parent_task_id"] = parent_task_id
            meta_dir = task_paths.task_dir(task_id)
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / ".task_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("write_task_meta_failed", task_id=task_id[:8], error=str(exc)[:200])
